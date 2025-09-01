"""Content-addressable immutable context system."""

import hashlib
import json
import redis
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from sqlalchemy.orm import Session

from ..models.database import get_database
from sqlalchemy import Column, String, JSON, DateTime, Text, create_engine
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from ..models.database import Base


class ImmutableContext(Base):
    """Immutable context storage - content addressable."""
    
    __tablename__ = "immutable_contexts"
    
    context_key = Column(String(16), primary_key=True)  # 64-bit hex key
    messages = Column(JSON, nullable=False)  # Message history
    context_metadata = Column(JSON, nullable=False, default=dict)
    content_hash = Column(String(64), nullable=False, index=True)  # Full SHA256 for verification
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_accessed = Column(DateTime, default=datetime.utcnow, nullable=False)


class ContentAddressableContextManager:
    """Manages immutable, content-addressable contexts."""
    
    def __init__(self, redis_client: Optional[redis.Redis] = None):
        self.redis = redis_client or redis.Redis(host='localhost', port=6379, db=1, decode_responses=True)
        self.cache_ttl = 86400  # 24 hours cache
    
    def generate_context_key(self, messages: List[Dict[str, str]]) -> Tuple[str, str]:
        """Generate content-addressable key for messages.
        
        Returns:
            (context_key, full_hash) - 16 char key and full 64 char hash
        """
        # Normalize messages for consistent hashing
        normalized = []
        for msg in messages:
            normalized.append({
                "role": msg.get("role", ""),
                "content": msg.get("content", "")
            })
        
        # Create deterministic JSON
        content = json.dumps(normalized, sort_keys=True, separators=(',', ':'))
        
        # Generate hashes
        full_hash = hashlib.sha256(content.encode()).hexdigest()
        context_key = full_hash[:16]  # 64-bit key
        
        return context_key, full_hash
    
    async def store_context(
        self, 
        messages: List[Dict[str, str]], 
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """Store immutable context and return key."""
        context_key, full_hash = self.generate_context_key(messages)
        
        # Check if already exists (immutable, so no need to update)
        if await self.context_exists(context_key):
            await self._update_access_time(context_key)
            return context_key
        
        # Store in cache
        cache_data = {
            "messages": messages,
            "metadata": metadata or {},
            "full_hash": full_hash,
            "created_at": datetime.utcnow().isoformat()
        }
        
        cache_key = f"ctx:{context_key}"
        try:
            self.redis.setex(cache_key, self.cache_ttl, json.dumps(cache_data))
        except:
            pass  # Cache failure shouldn't break functionality
        
        # Store in database
        db = next(get_database())
        try:
            context = ImmutableContext(
                context_key=context_key,
                messages=messages,
                context_metadata=metadata or {},
                content_hash=full_hash
            )
            
            db.merge(context)  # merge handles duplicates gracefully
            db.commit()
            
        except Exception as e:
            # If it's a duplicate key, that's fine (immutable contexts)
            db.rollback()
        finally:
            db.close()
        
        return context_key
    
    async def load_context(self, context_key: str) -> Optional[Dict[str, Any]]:
        """Load context by key."""
        # Try cache first
        cache_key = f"ctx:{context_key}"
        try:
            cached = self.redis.get(cache_key)
            if cached:
                data = json.loads(cached)
                await self._update_access_time(context_key)
                return data
        except:
            pass
        
        # Load from database
        db = next(get_database())
        try:
            context = db.query(ImmutableContext).filter(
                ImmutableContext.context_key == context_key
            ).first()
            
            if context:
                data = {
                    "messages": context.messages,
                    "metadata": context.context_metadata,
                    "full_hash": context.content_hash,
                    "created_at": context.created_at.isoformat()
                }
                
                # Update cache
                try:
                    self.redis.setex(cache_key, self.cache_ttl, json.dumps(data))
                except:
                    pass
                
                # Update access time
                context.last_accessed = datetime.utcnow()
                db.commit()
                
                return data
            
            return None
            
        finally:
            db.close()
    
    async def context_exists(self, context_key: str) -> bool:
        """Check if context exists."""
        # Check cache first
        cache_key = f"ctx:{context_key}"
        try:
            if self.redis.exists(cache_key):
                return True
        except:
            pass
        
        # Check database
        db = next(get_database())
        try:
            exists = db.query(ImmutableContext).filter(
                ImmutableContext.context_key == context_key
            ).first() is not None
            return exists
        finally:
            db.close()
    
    async def extend_context(
        self, 
        base_key: str, 
        new_messages: List[Dict[str, str]]
    ) -> str:
        """Create new context by extending existing one.
        
        Args:
            base_key: Previous context key
            new_messages: New messages to append
            
        Returns:
            New context key for extended conversation
        """
        # Load base context
        base_context = await self.load_context(base_key) if base_key else None
        base_messages = base_context["messages"] if base_context else []
        
        # Create extended message list
        extended_messages = base_messages + new_messages
        
        # Store and return new key
        return await self.store_context(extended_messages)
    
    async def _update_access_time(self, context_key: str):
        """Update last accessed time for context."""
        db = next(get_database())
        try:
            context = db.query(ImmutableContext).filter(
                ImmutableContext.context_key == context_key
            ).first()
            
            if context:
                context.last_accessed = datetime.utcnow()
                db.commit()
        except:
            db.rollback()
        finally:
            db.close()


# Global instance
content_addressable_context = ContentAddressableContextManager()