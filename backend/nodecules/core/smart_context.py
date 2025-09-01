"""Smart context management that adapts to provider capabilities."""

import json
import redis
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Union
from uuid import UUID, uuid4
from abc import ABC, abstractmethod
from sqlalchemy.orm import Session

from ..models.database import get_database, Base
from sqlalchemy import Column, String, DateTime, Integer, Text, JSON, Boolean, create_engine
from sqlalchemy.dialects.postgresql import UUID as PG_UUID


class SmartContext(Base):
    """Database model for smart contexts."""
    
    __tablename__ = "smart_contexts"
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    context_id = Column(String(100), unique=True, nullable=False, index=True)
    provider = Column(String(50), nullable=False)
    provider_context_data = Column(JSON, nullable=True)  # Provider-specific context info
    messages = Column(JSON, nullable=True)  # Full message history (for non-caching providers)
    context_metadata = Column(JSON, nullable=False, default=dict)
    turn_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_updated = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)


class ProviderCapabilities:
    """Define what each provider can do."""
    
    CAPABILITIES = {
        "bedrock": {
            "context_caching": True,
            "prompt_caching": True,
            "max_context_length": 100000
        },
        "anthropic": {
            "context_caching": True,
            "prompt_caching": True,
            "max_context_length": 100000
        },
        "openai": {
            "context_caching": False,
            "prompt_caching": False,
            "max_context_length": 32000
        },
        "ollama": {
            "context_caching": False,
            "prompt_caching": False,
            "max_context_length": 4000
        },
        "mock": {
            "context_caching": False,
            "prompt_caching": False,
            "max_context_length": 1000
        }
    }
    
    @classmethod
    def supports_caching(cls, provider: str) -> bool:
        return cls.CAPABILITIES.get(provider, {}).get("context_caching", False)
    
    @classmethod
    def get_max_context(cls, provider: str) -> int:
        return cls.CAPABILITIES.get(provider, {}).get("max_context_length", 4000)


class BaseProviderAdapter(ABC):
    """Base adapter for LLM providers."""
    
    @abstractmethod
    async def generate_with_context(
        self, 
        context_data: Dict[str, Any], 
        new_message: str, 
        **kwargs
    ) -> tuple[str, Dict[str, Any]]:
        """Generate response and return (response, updated_context_data)."""
        pass
    
    async def generate_with_context_streaming(
        self,
        context_data: Dict[str, Any],
        new_message: str,
        **kwargs
    ) -> tuple[Any, Dict[str, Any]]:
        """
        Generate streaming response and return (stream_generator, updated_context_data).
        
        The stream_generator yields text chunks as they arrive from the provider.
        Default implementation falls back to non-streaming.
        """
        # Fallback to non-streaming
        response, updated_context = await self.generate_with_context(
            context_data, new_message, **kwargs
        )
        
        # Yield the full response as a single chunk
        async def single_chunk_generator():
            yield response
        
        return single_chunk_generator(), updated_context
    
    @abstractmethod
    def create_new_context(self, system_prompt: str = None) -> Dict[str, Any]:
        """Create new context data for this provider."""
        pass


class OllamaAdapter(BaseProviderAdapter):
    """Ollama adapter - no caching, full message history."""
    
    def __init__(self, base_url: str = "http://host.docker.internal:11434"):
        self.base_url = base_url.rstrip("/")
    
    async def generate_with_context(
        self, 
        context_data: Dict[str, Any], 
        new_message: str,
        model: str = "llama2",
        temperature: float = 0.7,
        **kwargs
    ) -> tuple[str, Dict[str, Any]]:
        """Generate with full message history."""
        import httpx
        
        # Get message history
        messages = context_data.get("messages", [])
        
        # Add new user message (make a copy to avoid mutation)
        messages = messages + [{"role": "user", "content": new_message}]
        
        # Convert to Ollama prompt format
        prompt_parts = []
        for msg in messages:
            role = msg["role"]
            content = msg["content"]
            if role == "system":
                prompt_parts.append(f"System: {content}")
            elif role == "user":
                prompt_parts.append(f"Human: {content}")
            elif role == "assistant":
                prompt_parts.append(f"Assistant: {content}")
        
        prompt_parts.append("Assistant:")
        full_prompt = "\n\n".join(prompt_parts)
        
        # Call Ollama
        async with httpx.AsyncClient(timeout=1200.0) as client:  # 20 minutes
            response = await client.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": model,
                    "prompt": full_prompt,
                    "stream": False,
                    "options": {
                        "temperature": temperature
                    }
                }
            )
            
            if response.status_code == 200:
                result = response.json()
                ai_response = result.get("response", "No response").strip()
            else:
                ai_response = f"Ollama error: HTTP {response.status_code}"
        
        # Add assistant response to messages
        messages.append({"role": "assistant", "content": ai_response})
        
        # Return response and updated context
        updated_context = {
            "messages": messages,
            "last_model": model,
            "last_temperature": temperature
        }
        
        return ai_response, updated_context
    
    async def generate_with_context_streaming(
        self,
        context_data: Dict[str, Any],
        new_message: str,
        model: str = "llama2",
        temperature: float = 0.7,
        **kwargs
    ) -> tuple[Any, Dict[str, Any]]:
        """Generate streaming response with Ollama."""
        import httpx
        import json
        
        # Get message history
        messages = context_data.get("messages", [])
        
        # Add new user message (make a copy to avoid mutation)
        messages = messages + [{"role": "user", "content": new_message}]
        
        # Convert to Ollama prompt format
        prompt_parts = []
        for msg in messages:
            role = msg["role"]
            content = msg["content"]
            if role == "system":
                prompt_parts.append(f"System: {content}")
            elif role == "user":
                prompt_parts.append(f"Human: {content}")
            elif role == "assistant":
                prompt_parts.append(f"Assistant: {content}")
        
        prompt_parts.append("Assistant:")
        full_prompt = "\n\n".join(prompt_parts)
        
        # Prepare updated context
        updated_context = {
            "messages": messages,  # Will be updated with full response later
            "last_model": model,
            "last_temperature": temperature
        }
        
        async def ollama_stream_generator():
            full_response = ""
            try:
                async with httpx.AsyncClient(timeout=1200.0) as client:  # 20 minutes
                    async with client.stream(
                        "POST",
                        f"{self.base_url}/api/generate",
                        json={
                            "model": model,
                            "prompt": full_prompt,
                            "stream": True,
                            "options": {
                                "temperature": temperature
                            }
                        }
                    ) as response:
                        if response.status_code != 200:
                            yield f"Ollama error: HTTP {response.status_code}"
                            return
                        
                        async for line in response.aiter_lines():
                            if line.strip():
                                try:
                                    chunk_data = json.loads(line)
                                    if "response" in chunk_data:
                                        text_chunk = chunk_data["response"]
                                        full_response += text_chunk
                                        yield text_chunk
                                    
                                    # Check if done
                                    if chunk_data.get("done", False):
                                        break
                                        
                                except json.JSONDecodeError:
                                    continue
                                    
            except Exception as e:
                yield f"Streaming error: {str(e)}"
                return
            
            # Update context with complete response
            updated_context["messages"].append({
                "role": "assistant", 
                "content": full_response
            })
        
        return ollama_stream_generator(), updated_context
    
    def create_new_context(self, system_prompt: str = None) -> Dict[str, Any]:
        """Create new context for Ollama."""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        
        return {
            "messages": messages,
            "provider_type": "full_history"
        }


class MockAdapter(BaseProviderAdapter):
    """Mock adapter for testing."""
    
    async def generate_with_context(
        self, 
        context_data: Dict[str, Any], 
        new_message: str,
        model: str = "mock",
        **kwargs
    ) -> tuple[str, Dict[str, Any]]:
        """Mock generation."""
        messages = context_data.get("messages", [])
        turn_count = len([m for m in messages if m["role"] == "user"]) + 1
        
        response = f"[MOCK {model.upper()}] Turn {turn_count} response to: {new_message}"
        
        # Update context
        messages.extend([
            {"role": "user", "content": new_message},
            {"role": "assistant", "content": response}
        ])
        
        return response, {"messages": messages}
    
    def create_new_context(self, system_prompt: str = None) -> Dict[str, Any]:
        """Create mock context."""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        return {"messages": messages}


class SmartContextManager:
    """Context manager that adapts to provider capabilities."""
    
    def __init__(self, redis_client: Optional[redis.Redis] = None):
        self.redis = redis_client or redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
        self.cache_ttl = 3600  # 1 hour
        
        # Provider adapters
        self.adapters = {
            "ollama": OllamaAdapter(),
            "mock": MockAdapter(),
            # TODO: Add Bedrock, Anthropic adapters when needed
        }
    
    def _get_cache_key(self, context_id: str) -> str:
        return f"smart_context:{context_id}"
    
    async def create_context(
        self, 
        provider: str,
        system_prompt: str = None,
        conversation_id: str = None
    ) -> str:
        """Create a new smart context."""
        context_id = conversation_id or str(uuid4())
        
        # Get provider adapter
        adapter = self.adapters.get(provider)
        if not adapter:
            raise ValueError(f"Unsupported provider: {provider}")
        
        # Create provider-specific context
        provider_context_data = adapter.create_new_context(system_prompt)
        
        # Store in database
        db = next(get_database())
        try:
            smart_context = SmartContext(
                context_id=context_id,
                provider=provider,
                provider_context_data=provider_context_data,
                messages=provider_context_data.get("messages", []),
                context_metadata={
                    "system_prompt": system_prompt,
                    "supports_caching": ProviderCapabilities.supports_caching(provider)
                }
            )
            
            db.add(smart_context)
            db.commit()
            
            # Cache it
            self._cache_context(context_id, smart_context)
            
            return context_id
            
        finally:
            db.close()
    
    async def continue_conversation(
        self,
        context_id: str,
        new_message: str,
        model: str = None,
        **provider_kwargs
    ) -> tuple[str, str]:
        """Continue conversation and return (response, context_id)."""
        
        # Load context
        context = await self._load_context(context_id)
        if not context:
            raise ValueError(f"Context {context_id} not found")
        
        # Get provider adapter
        adapter = self.adapters.get(context.provider)
        if not adapter:
            raise ValueError(f"Unsupported provider: {context.provider}")
        
        # Generate response
        response, updated_provider_data = await adapter.generate_with_context(
            context.provider_context_data,
            new_message,
            model=model or "default",
            **provider_kwargs
        )
        
        # Update context in database
        context.provider_context_data = updated_provider_data
        context.messages = updated_provider_data.get("messages", context.messages)
        context.turn_count += 1
        context.last_updated = datetime.utcnow()
        
        db = next(get_database())
        try:
            db.merge(context)
            db.commit()
            
            # Update cache
            self._cache_context(context_id, context)
            
            return response, context_id
            
        finally:
            db.close()
    
    async def continue_conversation_streaming(
        self,
        context_id: str,
        new_message: str,
        model: str = None,
        **provider_kwargs
    ) -> tuple[Any, str]:
        """Continue conversation with streaming and return (stream_generator, context_id)."""
        
        # Load context
        context = await self._load_context(context_id)
        if not context:
            raise ValueError(f"Context {context_id} not found")
        
        # Get provider adapter
        adapter = self.adapters.get(context.provider)
        if not adapter:
            raise ValueError(f"Unsupported provider: {context.provider}")
        
        # Generate streaming response
        stream_generator, updated_provider_data = await adapter.generate_with_context_streaming(
            context.provider_context_data,
            new_message,
            model=model or "default",
            **provider_kwargs
        )
        
        # Create a wrapper that updates context when streaming completes
        async def context_updating_stream():
            async for chunk in stream_generator:
                yield chunk
            
            # Update context after streaming completes
            context.provider_context_data = updated_provider_data
            context.messages = updated_provider_data.get("messages", context.messages)
            context.turn_count += 1
            context.last_updated = datetime.utcnow()
            
            db = next(get_database())
            try:
                db.merge(context)
                db.commit()
                
                # Update cache
                self._cache_context(context_id, context)
                
            finally:
                db.close()
        
        return context_updating_stream(), context_id
    
    async def get_context_info(self, context_id: str) -> Optional[Dict[str, Any]]:
        """Get context information for external systems."""
        context = await self._load_context(context_id)
        if not context:
            return None
        
        return {
            "context_id": context.context_id,
            "provider": context.provider,
            "turn_count": context.turn_count,
            "supports_caching": ProviderCapabilities.supports_caching(context.provider),
            "messages": context.messages,
            "created_at": context.created_at.isoformat(),
            "last_updated": context.last_updated.isoformat(),
            "metadata": context.metadata
        }
    
    async def _load_context(self, context_id: str) -> Optional[SmartContext]:
        """Load context from cache or database."""
        # Try cache first
        cache_key = self._get_cache_key(context_id)
        try:
            cached_data = self.redis.get(cache_key)
            if cached_data:
                data = json.loads(cached_data)
                context = SmartContext()
                for key, value in data.items():
                    if key in ['created_at', 'last_updated']:
                        setattr(context, key, datetime.fromisoformat(value))
                    else:
                        setattr(context, key, value)
                return context
        except Exception:
            pass
        
        # Load from database
        db = next(get_database())
        try:
            context = db.query(SmartContext).filter(
                SmartContext.context_id == context_id,
                SmartContext.is_active == True
            ).first()
            
            if context:
                self._cache_context(context_id, context)
            
            return context
            
        finally:
            db.close()
    
    def _cache_context(self, context_id: str, context: SmartContext):
        """Cache context data."""
        try:
            cache_data = {
                "id": str(context.id),
                "context_id": context.context_id,
                "provider": context.provider,
                "provider_context_data": context.provider_context_data,
                "messages": context.messages,
                "metadata": context.context_metadata,
                "turn_count": context.turn_count,
                "created_at": context.created_at.isoformat(),
                "last_updated": context.last_updated.isoformat(),
                "is_active": context.is_active
            }
            
            cache_key = self._get_cache_key(context_id)
            self.redis.setex(cache_key, self.cache_ttl, json.dumps(cache_data))
        except Exception:
            pass  # Cache failure shouldn't break functionality


# Global instance
smart_context_manager = SmartContextManager()