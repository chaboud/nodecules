"""Immutable smart chat node using content-addressable contexts."""

from typing import Dict, Any, List, AsyncGenerator

from ..core.types import NodeSpec, PortSpec, ParameterSpec, DataType, ExecutionContext, NodeData, BaseNode
from ..core.content_addressable_context import content_addressable_context
from ..core.smart_context import OllamaAdapter


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
                    description="User message"
                ),
                PortSpec(
                    name="context_key",
                    data_type=DataType.TEXT,
                    required=False,
                    description="Previous context key (optional)"
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
                    name="message_count",
                    data_type=DataType.TEXT,
                    description="Total messages in context"
                )
            ],
            parameters=[
                ParameterSpec(
                    name="provider",
                    data_type="select",
                    default="ollama",
                    description="LLM provider",
                    constraints={"options": ["ollama", "anthropic"]}
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
                )
            ]
        )
        super().__init__(spec)
        
        # Initialize Ollama adapter
        self.ollama = OllamaAdapter()
    
    async def execute(self, context: ExecutionContext, node_data: NodeData) -> Dict[str, Any]:
        """Execute immutable chat with content-addressable contexts."""
        # Get inputs
        message = context.get_input_value(node_data.node_id, "message")
        prev_context_key = context.get_input_value(node_data.node_id, "context_key")
        
        # Also check for injected context key from instance execution
        if not prev_context_key:
            prev_context_key = context.execution_inputs.get("_context_key")
        
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
        
        if not message:
            return {
                "response": "Error: No message provided",
                "context_key": prev_context_key or "empty",
                "message_count": "0"
            }
        
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
            
            if streaming:
                # Generate streaming response
                stream_generator, _ = await self.ollama.generate_with_context_streaming(
                    context_data=context_data,
                    new_message=message,
                    model=model,
                    temperature=temperature
                )
                
                # Collect full response from stream
                response_parts = []
                async for chunk in stream_generator:
                    response_parts.append(chunk)
                
                response = "".join(response_parts)
                
            else:
                # Generate response using non-streaming
                response, _ = await self.ollama.generate_with_context(
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
            
            # Store new immutable context
            new_context_key = await content_addressable_context.store_context(new_messages)
            
            return {
                "response": response,
                "context_key": new_context_key,
                "message_count": str(len(new_messages))
            }
            
        except Exception as e:
            return {
                "response": f"Error: {str(e)}",
                "context_key": prev_context_key or "error",
                "message_count": "0"
            }
    
    async def execute_streaming(self, context: ExecutionContext, node_data: NodeData) -> AsyncGenerator[str, None]:
        """Execute with streaming response."""
        # Get inputs
        message = context.get_input_value(node_data.node_id, "message")
        prev_context_key = context.get_input_value(node_data.node_id, "context_key")
        
        # Also check for injected context key from instance execution
        if not prev_context_key:
            prev_context_key = context.execution_inputs.get("_context_key")
        
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