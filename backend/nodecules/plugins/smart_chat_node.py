"""Simple chat node using smart context management."""

from typing import Dict, Any

from ..core.types import NodeSpec, PortSpec, ParameterSpec, DataType, ExecutionContext, NodeData, BaseNode
from ..core.smart_context import smart_context_manager


class SmartChatNode(BaseNode):
    """Chat node that uses smart context management."""
    
    NODE_TYPE = "smart_chat"
    
    def __init__(self):
        spec = NodeSpec(
            node_type=self.NODE_TYPE,
            display_name="Smart Chat",
            description="Chat with smart context management (adapts to provider capabilities)",
            category="AI/Chat", 
            inputs=[
                PortSpec(
                    name="message",
                    data_type=DataType.TEXT,
                    description="User message"
                ),
                PortSpec(
                    name="context_id", 
                    data_type=DataType.TEXT,
                    required=False,
                    description="Context ID for conversation continuity (optional)"
                ),
                PortSpec(
                    name="context_data",
                    data_type=DataType.JSON,
                    required=False,
                    description="Previous context data (optional, alternative to context_id)"
                )
            ],
            outputs=[
                PortSpec(
                    name="response",
                    data_type=DataType.TEXT,
                    description="AI response"
                ),
                PortSpec(
                    name="context_id",
                    data_type=DataType.TEXT, 
                    description="Context ID for next turn"
                ),
                PortSpec(
                    name="context_data",
                    data_type=DataType.JSON,
                    description="Full context data (for inspection or passthrough)"
                )
            ],
            parameters=[
                ParameterSpec(
                    name="provider",
                    data_type="select",
                    default="ollama",
                    description="LLM provider",
                    constraints={"options": ["ollama", "anthropic", "bedrock", "mock"]}
                ),
                ParameterSpec(
                    name="model", 
                    data_type="string",
                    default="llama2",
                    description="Model name"
                ),
                ParameterSpec(
                    name="system_prompt",
                    data_type="text",
                    default="You are a helpful AI assistant.",
                    description="System prompt for new conversations"
                ),
                ParameterSpec(
                    name="temperature",
                    data_type="number", 
                    default=0.7,
                    description="Response temperature",
                    constraints={"min": 0.0, "max": 2.0}
                ),
                ParameterSpec(
                    name="context_data_dominant",
                    data_type="boolean",
                    default=False,
                    description="Use context_data over context_id when both provided"
                )
            ]
        )
        super().__init__(spec)
    
    async def execute(self, context: ExecutionContext, node_data: NodeData) -> Dict[str, Any]:
        """Execute smart chat node."""
        # Get inputs
        message = context.get_input_value(node_data.node_id, "message")
        context_id = context.get_input_value(node_data.node_id, "context_id")
        context_data = context.get_input_value(node_data.node_id, "context_data")
        
        # Get parameters
        params = node_data.parameters
        provider = params.get("provider", "ollama")
        model = params.get("model", "llama2")
        system_prompt = params.get("system_prompt", "You are a helpful AI assistant.")
        temperature = float(params.get("temperature", 0.7))
        context_data_dominant = params.get("context_data_dominant", False)
        
        if not message:
            return {
                "response": "Error: No message provided",
                "context_id": context_id or "error",
                "context_data": {"messages": [], "error": "no_message"}
            }
        
        try:
            # Context supremacy logic for smart_chat
            effective_context_id = None
            context_source = "fresh"
            
            # Apply supremacy model
            if context_data and context_id:
                # Both provided - check dominance setting
                if context_data_dominant:
                    # Try to use context_data directly (convert to smart context)
                    if isinstance(context_data, dict) and "messages" in context_data:
                        # Create temporary context from data
                        effective_context_id = await smart_context_manager.create_context(
                            provider=provider,
                            system_prompt=system_prompt
                        )
                        # Update with messages from context_data
                        # This is a simplified approach - full implementation would sync the context
                        context_source = "context_data"
                    else:
                        # Invalid context_data, fall back to context_id
                        effective_context_id = context_id
                        context_source = "context_id_fallback"
                else:
                    # context_id dominant (default)
                    effective_context_id = context_id
                    context_source = "context_id"
            elif context_data:
                # Only context_data provided
                if isinstance(context_data, dict) and "messages" in context_data:
                    # Create temporary context from data
                    effective_context_id = await smart_context_manager.create_context(
                        provider=provider,
                        system_prompt=system_prompt
                    )
                    context_source = "context_data"
            elif context_id:
                # Only context_id provided
                effective_context_id = context_id
                context_source = "context_id"
            # If neither provided, effective_context_id stays None (fresh start)
            
            if effective_context_id:
                # Continue existing conversation
                response, final_context_id = await smart_context_manager.continue_conversation(
                    context_id=effective_context_id,
                    new_message=message,
                    model=model,
                    temperature=temperature
                )
            else:
                # Create new conversation
                final_context_id = await smart_context_manager.create_context(
                    provider=provider,
                    system_prompt=system_prompt
                )
                
                # Now continue with the first message
                response, final_context_id = await smart_context_manager.continue_conversation(
                    context_id=final_context_id,
                    new_message=message,
                    model=model,
                    temperature=temperature
                )
                context_source = "fresh"
            
            # Get context data for output
            try:
                # Load the context data from smart_context_manager for output
                context_record = await smart_context_manager.get_context(final_context_id)
                output_context_data = {
                    "messages": context_record.get("messages", []),
                    "provider": provider,
                    "model": model,
                    "context_source": context_source
                }
            except:
                # Fallback if context retrieval fails
                output_context_data = {
                    "messages": [],
                    "provider": provider,
                    "model": model,
                    "context_source": context_source,
                    "error": "context_retrieval_failed"
                }
            
            return {
                "response": response,
                "context_id": final_context_id,
                "context_data": output_context_data
            }
            
        except Exception as e:
            return {
                "response": f"Error: {str(e)}",
                "context_id": context_id or "error",
                "context_data": {"messages": [], "error": str(e)}
            }


# Registry
SMART_CHAT_NODES = {
    "smart_chat": SmartChatNode,
}