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
                )
            ],
            parameters=[
                ParameterSpec(
                    name="provider",
                    data_type="select",
                    default="ollama",
                    description="LLM provider",
                    constraints={"options": ["ollama", "anthropic", "mock"]}
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
                )
            ]
        )
        super().__init__(spec)
    
    async def execute(self, context: ExecutionContext, node_data: NodeData) -> Dict[str, Any]:
        """Execute smart chat node."""
        # Get inputs
        message = context.get_input_value(node_data.node_id, "message")
        context_id = context.get_input_value(node_data.node_id, "context_id")
        
        # Get parameters
        params = node_data.parameters
        provider = params.get("provider", "ollama")
        model = params.get("model", "llama2")
        system_prompt = params.get("system_prompt", "You are a helpful AI assistant.")
        temperature = float(params.get("temperature", 0.7))
        
        if not message:
            return {
                "response": "Error: No message provided",
                "context_id": context_id or "error"
            }
        
        try:
            if context_id:
                # Continue existing conversation
                response, context_id = await smart_context_manager.continue_conversation(
                    context_id=context_id,
                    new_message=message,
                    model=model,
                    temperature=temperature
                )
            else:
                # Create new conversation
                context_id = await smart_context_manager.create_context(
                    provider=provider,
                    system_prompt=system_prompt
                )
                
                # Now continue with the first message
                response, context_id = await smart_context_manager.continue_conversation(
                    context_id=context_id,
                    new_message=message,
                    model=model,
                    temperature=temperature
                )
            
            return {
                "response": response,
                "context_id": context_id
            }
            
        except Exception as e:
            return {
                "response": f"Error: {str(e)}",
                "context_id": context_id or "error"
            }


# Registry
SMART_CHAT_NODES = {
    "smart_chat": SmartChatNode,
}