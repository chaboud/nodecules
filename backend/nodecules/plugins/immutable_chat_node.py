"""Immutable smart chat node using content-addressable contexts."""

from typing import Dict, Any, List, AsyncGenerator

from ..core.types import NodeSpec, PortSpec, ParameterSpec, DataType, ExecutionContext, NodeData, BaseNode
from ..core.content_addressable_context import content_addressable_context
from ..core.smart_context import OllamaAdapter, AnthropicAdapter


class ImmutableChatNode(BaseNode):
    """Chat node with immutable, content-addressable contexts."""
    
    NODE_TYPE = "immutable_chat"
    
    def __init__(self):
        spec = NodeSpec(
            node_type=self.NODE_TYPE,
            display_name="Immutable Chat",
            description="Chat with immutable, content-addressable context management",
            category="AI/Chat",
            inputs=[
                PortSpec(
                    name="message",
                    data_type=DataType.TEXT,
                    required=False,
                    description="User message (optional, uses node parameter if not connected)"
                ),
                PortSpec(
                    name="context_key",
                    data_type=DataType.TEXT,
                    required=False,
                    description="Previous context key (optional)"
                ),
                PortSpec(
                    name="context_data",
                    data_type=DataType.JSON,
                    required=False,
                    description="Previous context data (optional, alternative to context_key)"
                ),
                # Optional parameter inputs - can be connected or use node defaults
                PortSpec(
                    name="model",
                    data_type=DataType.TEXT,
                    required=False,
                    description="Model name (optional, uses node parameter if not connected)"
                ),
                PortSpec(
                    name="system_prompt",
                    data_type=DataType.TEXT,
                    required=False,
                    description="System prompt (optional, uses node parameter if not connected)"
                ),
                PortSpec(
                    name="temperature",
                    data_type=DataType.TEXT,
                    required=False,
                    description="Temperature (optional, uses node parameter if not connected)"
                ),
                PortSpec(
                    name="provider",
                    data_type=DataType.TEXT,
                    required=False,
                    description="Provider (optional, uses node parameter if not connected)"
                )
            ],
            outputs=[
                PortSpec(
                    name="response",
                    data_type=DataType.TEXT,
                    description="AI response"
                ),
                PortSpec(
                    name="context_key",
                    data_type=DataType.TEXT,
                    description="New context key for next turn"
                ),
                PortSpec(
                    name="context_data",
                    data_type=DataType.JSON,
                    description="Full context data (for inspection or passthrough)"
                ),
                PortSpec(
                    name="message_count",
                    data_type=DataType.TEXT,
                    description="Total messages in context"
                )
            ],
            parameters=[
                ParameterSpec(
                    name="message",
                    data_type="text",
                    default="",
                    description="User message (used if input not connected)"
                ),
                ParameterSpec(
                    name="provider",
                    data_type="select",
                    default="ollama",
                    description="LLM provider",
                    constraints={"options": ["ollama", "anthropic", "bedrock"]}
                ),
                ParameterSpec(
                    name="model",
                    data_type="string",
                    default="llama3.2:3b",
                    description="Model name"
                ),
                ParameterSpec(
                    name="system_prompt",
                    data_type="text",
                    default="You are a helpful AI assistant.",
                    description="System prompt"
                ),
                ParameterSpec(
                    name="temperature",
                    data_type="number",
                    default=0.7,
                    description="Response temperature",
                    constraints={"min": 0.0, "max": 2.0}
                ),
                ParameterSpec(
                    name="streaming",
                    data_type="boolean",
                    default=False,
                    description="Enable streaming response"
                ),
                ParameterSpec(
                    name="context_data_dominant",
                    data_type="boolean",
                    default=False,
                    description="Use context_data over context_key when both provided"
                )
            ]
        )
        super().__init__(spec)
        
        # Initialize provider adapters
        self.adapters = {
            "ollama": OllamaAdapter(),
            "anthropic": AnthropicAdapter()
        }
        
        # Add bedrock if available
        try:
            from ..core.smart_context import BedrockAdapter
            self.adapters["bedrock"] = BedrockAdapter()
        except ImportError:
            pass  # Bedrock not available
    
    async def execute(self, context: ExecutionContext, node_data: NodeData) -> Dict[str, Any]:
        """Execute immutable chat with content-addressable contexts."""
        # Get inputs with parameter fallback for message
        message = context.get_input_value(node_data.node_id, "message")
        if message is None:
            message = node_data.parameters.get("message", "")
        
        prev_context_key = context.get_input_value(node_data.node_id, "context_key")
        prev_context_data = context.get_input_value(node_data.node_id, "context_data")
        
        # No global fallback - if no context_key input is connected, start fresh
        # This ensures explicit behavior and prevents context bleeding between LLMs
        
        # Get parameters with input override support
        params = node_data.parameters
        
        # Use input values if connected, otherwise fall back to node parameters
        model = context.get_input_value(node_data.node_id, "model") or params.get("model", "llama3.2:3b")
        system_prompt = context.get_input_value(node_data.node_id, "system_prompt") or params.get("system_prompt", "You are a helpful AI assistant.")
        temperature = context.get_input_value(node_data.node_id, "temperature")
        if temperature is not None:
            try:
                temperature = float(temperature)
            except (ValueError, TypeError):
                temperature = params.get("temperature", 0.7)
        else:
            temperature = params.get("temperature", 0.7)
        
        provider = context.get_input_value(node_data.node_id, "provider") or params.get("provider", "ollama")
        streaming = params.get("streaming", False)
        context_data_dominant = params.get("context_data_dominant", False)
        
        if not message:
            return {
                "response": "Error: No message provided",
                "context_key": prev_context_key or "empty",
                "context_data": {"messages": [], "error": "no_message"},
                "message_count": "0"
            }
        
        try:
            # Context supremacy logic implementation
            prev_messages = []
            context_source = "fresh"  # Track where context came from
            
            # Apply supremacy model
            if prev_context_data and prev_context_key:
                # Both provided - check dominance setting
                if context_data_dominant:
                    if isinstance(prev_context_data, dict) and "messages" in prev_context_data:
                        prev_messages = prev_context_data["messages"]
                        context_source = "context_data"
                    else:
                        # Invalid context_data, fall back to context_key
                        prev_context = await content_addressable_context.load_context(prev_context_key)
                        if prev_context:
                            prev_messages = prev_context["messages"]
                            context_source = "context_key_fallback"
                else:
                    # context_key dominant (default)
                    prev_context = await content_addressable_context.load_context(prev_context_key)
                    if prev_context:
                        prev_messages = prev_context["messages"]
                        context_source = "context_key"
                    else:
                        # context_key failed, fall back to context_data
                        if isinstance(prev_context_data, dict) and "messages" in prev_context_data:
                            prev_messages = prev_context_data["messages"]
                            context_source = "context_data_fallback"
            elif prev_context_data:
                # Only context_data provided
                if isinstance(prev_context_data, dict) and "messages" in prev_context_data:
                    prev_messages = prev_context_data["messages"]
                    context_source = "context_data"
            elif prev_context_key:
                # Only context_key provided
                prev_context = await content_addressable_context.load_context(prev_context_key)
                if prev_context:
                    prev_messages = prev_context["messages"]
                    context_source = "context_key"
            # If neither provided, prev_messages stays empty (fresh start)
            
            # If no previous messages, start with system prompt
            if not prev_messages:
                prev_messages = [{"role": "system", "content": system_prompt}]
                context_source = "fresh"
            
            # Get provider adapter
            adapter = self.adapters.get(provider)
            if not adapter:
                return {
                    "response": f"Error: Unsupported provider '{provider}'",
                    "context_key": prev_context_key or "error",
                    "context_data": {"messages": prev_messages, "error": f"provider_{provider}_not_found"},
                    "message_count": str(len(prev_messages))
                }
            
            # Create context data for provider
            context_data = {
                "messages": prev_messages,
                "provider_type": "full_history"
            }
            
            if streaming:
                # Generate streaming response
                if hasattr(adapter, 'generate_with_context_streaming'):
                    stream_generator, _ = await adapter.generate_with_context_streaming(
                        context_data=context_data,
                        new_message=message,
                        model=model,
                        temperature=temperature
                    )
                    
                    # Collect full response from stream
                    response_parts = []
                    async for chunk in stream_generator:
                        if isinstance(chunk, str):
                            response_parts.append(chunk)
                        elif isinstance(chunk, dict) and chunk.get('_context_update'):
                            # Handle context update from streaming
                            context_data = chunk
                    
                    response = "".join(response_parts)
                else:
                    # Fall back to non-streaming if streaming not supported
                    response, _ = await adapter.generate_with_context(
                        context_data=context_data,
                        new_message=message,
                        model=model,
                        temperature=temperature
                    )
            else:
                # Generate response using non-streaming
                response, _ = await adapter.generate_with_context(
                    context_data=context_data,
                    new_message=message,
                    model=model,
                    temperature=temperature
                )
            
            # Create new message list with the conversation
            new_messages = prev_messages + [
                {"role": "user", "content": message},
                {"role": "assistant", "content": response}
            ]
            
            # Create full context data for output
            output_context_data = {
                "messages": new_messages,
                "provider_type": "full_history",
                "provider": provider,
                "model": model,
                "context_source": context_source
            }
            
            # Store new immutable context
            new_context_key = await content_addressable_context.store_context(new_messages)
            
            return {
                "response": response,
                "context_key": new_context_key,
                "context_data": output_context_data,
                "message_count": str(len(new_messages))
            }
            
        except Exception as e:
            return {
                "response": f"Error: {str(e)}",
                "context_key": prev_context_key or "error",
                "context_data": {"messages": [], "error": str(e)},
                "message_count": "0"
            }
    
    async def execute_streaming(self, context: ExecutionContext, node_data: NodeData) -> AsyncGenerator[str, None]:
        """Execute with streaming response."""
        # Get inputs with parameter fallback for message
        message = context.get_input_value(node_data.node_id, "message")
        if message is None:
            message = node_data.parameters.get("message", "")
        
        prev_context_key = context.get_input_value(node_data.node_id, "context_key")
        
        # No global fallback - if no context_key input is connected, start fresh
        # This ensures explicit behavior and prevents context bleeding between LLMs
        
        # Get parameters with input override support
        params = node_data.parameters
        
        # Use input values if connected, otherwise fall back to node parameters
        model = context.get_input_value(node_data.node_id, "model") or params.get("model", "llama3.2:3b")
        system_prompt = context.get_input_value(node_data.node_id, "system_prompt") or params.get("system_prompt", "You are a helpful AI assistant.")
        temperature = context.get_input_value(node_data.node_id, "temperature")
        if temperature is not None:
            try:
                temperature = float(temperature)
            except (ValueError, TypeError):
                temperature = params.get("temperature", 0.7)
        else:
            temperature = params.get("temperature", 0.7)
        
        provider = context.get_input_value(node_data.node_id, "provider") or params.get("provider", "ollama")
        
        if not message:
            yield "Error: No message provided"
            return
        
        try:
            # Load previous context if exists
            prev_messages = []
            if prev_context_key:
                prev_context = await content_addressable_context.load_context(prev_context_key)
                if prev_context:
                    prev_messages = prev_context["messages"]
            
            # If no previous messages, start with system prompt
            if not prev_messages:
                prev_messages = [{"role": "system", "content": system_prompt}]
            
            # Create context data for Ollama
            context_data = {
                "messages": prev_messages,
                "provider_type": "full_history"
            }
            
            # Generate streaming response
            stream_generator, _ = await self.ollama.generate_with_context_streaming(
                context_data=context_data,
                new_message=message,
                model=model,
                temperature=temperature
            )
            
            # Stream the response
            response_parts = []
            async for chunk in stream_generator:
                response_parts.append(chunk)
                yield chunk
            
            # Final response will be handled by regular execute method
            
        except Exception as e:
            yield f"Error: {str(e)}"


# Registry
IMMUTABLE_CHAT_NODES = {
    "immutable_chat": ImmutableChatNode,
}