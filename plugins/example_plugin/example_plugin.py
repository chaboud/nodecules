"""Example plugin for Nodecules."""

from typing import Any, Dict
from nodecules.core.types import BaseNode, DataType, NodeSpec, PortSpec, ParameterSpec, ExecutionContext, NodeData


class ExampleProcessorNode(BaseNode):
    """Example node that processes text with various operations."""
    
    NODE_TYPE = "example_processor"
    
    def __init__(self):
        spec = NodeSpec(
            node_type=self.NODE_TYPE,
            display_name="Example Processor",
            description="Example node for text processing demonstration",
            category="Examples",
            inputs=[
                PortSpec(
                    name="text", 
                    data_type=DataType.TEXT, 
                    description="Input text to process"
                )
            ],
            outputs=[
                PortSpec(
                    name="processed", 
                    data_type=DataType.TEXT, 
                    description="Processed text output"
                ),
                PortSpec(
                    name="length", 
                    data_type=DataType.JSON, 
                    description="Text length information"
                )
            ],
            parameters=[
                ParameterSpec(
                    name="operation",
                    data_type="string",
                    default="word_count",
                    description="Processing operation to perform",
                    constraints={
                        "enum": ["word_count", "char_count", "reverse", "shuffle"]
                    }
                ),
                ParameterSpec(
                    name="prefix",
                    data_type="string", 
                    default="",
                    description="Prefix to add to processed text"
                )
            ]
        )
        super().__init__(spec)
        
    async def execute(self, context: ExecutionContext, node_data: NodeData) -> Dict[str, Any]:
        # Get inputs
        text = context.get_input_value(node_data.node_id, "text") or ""
        
        # Get parameters
        operation = node_data.parameters.get("operation", "word_count")
        prefix = node_data.parameters.get("prefix", "")
        
        # Process based on operation
        if operation == "word_count":
            word_count = len(text.split())
            processed = f"{prefix}Word count: {word_count}"
            length_info = {"words": word_count, "chars": len(text)}
            
        elif operation == "char_count":
            char_count = len(text)
            processed = f"{prefix}Character count: {char_count}" 
            length_info = {"chars": char_count, "words": len(text.split())}
            
        elif operation == "reverse":
            processed = f"{prefix}{text[::-1]}"
            length_info = {"original_length": len(text)}
            
        elif operation == "shuffle":
            import random
            words = text.split()
            random.shuffle(words)
            processed = f"{prefix}{' '.join(words)}"
            length_info = {"word_count": len(words)}
            
        else:
            processed = f"{prefix}{text}"
            length_info = {}
            
        return {
            "processed": processed,
            "length": length_info
        }