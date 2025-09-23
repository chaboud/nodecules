"""Example custom plugin that should be auto-discovered."""

from typing import Any, Dict
from nodecules.core.types import BaseNode, DataType, NodeSpec, ParameterSpec, PortSpec, ExecutionContext, NodeData


class ExampleProcessorNode(BaseNode):
    """Example custom processing node that demonstrates auto-discovery."""
    
    NODE_TYPE = "example_processor"
    
    def __init__(self):
        spec = NodeSpec(
            node_type=self.NODE_TYPE,
            display_name="Example Processor",
            description="Example custom node for testing auto-discovery",
            category="Custom",
            inputs=[
                PortSpec(name="input", data_type=DataType.TEXT, description="Input text to process")
            ],
            outputs=[
                PortSpec(name="processed", data_type=DataType.TEXT, description="Processed text"),
                PortSpec(name="count_string", data_type=DataType.TEXT, description="Word count as string")
            ],
            parameters=[
                ParameterSpec(
                    name="processing_type",
                    data_type="string",
                    default="word_count",
                    description="Type of processing to perform",
                    constraints={"enum": ["word_count", "char_count", "reverse"]}
                )
            ]
        )
        super().__init__(spec)
        
    async def execute(self, context: ExecutionContext, node_data: NodeData) -> Dict[str, Any]:
        input_text = context.get_input_value(node_data.node_id, "input") or ""
        processing_type = node_data.parameters.get("processing_type", "word_count")
        
        if processing_type == "word_count":
            word_count = len(input_text.split()) if input_text.strip() else 0
            return {
                "processed": f"Words: {word_count}",
                "count_string": f"{word_count} words"
            }
        elif processing_type == "char_count":
            char_count = len(input_text)
            return {
                "processed": f"Characters: {char_count}",
                "count_string": f"{char_count} characters"
            }
        elif processing_type == "reverse":
            reversed_text = input_text[::-1]
            return {
                "processed": reversed_text,
                "count_string": f"Reversed {len(input_text)} characters"
            }
        else:
            return {
                "processed": input_text,
                "count_string": "0 words"
            }


class SimpleTextProcessorNode(BaseNode):
    """Another example node for testing multiple nodes in one file."""
    
    NODE_TYPE = "simple_text_processor"
    
    def __init__(self):
        spec = NodeSpec(
            node_type=self.NODE_TYPE,
            display_name="Simple Text Processor",
            description="Simple text processing operations",
            category="Custom",
            inputs=[
                PortSpec(name="text", data_type=DataType.TEXT, description="Input text")
            ],
            outputs=[
                PortSpec(name="result", data_type=DataType.TEXT, description="Processed result")
            ],
            parameters=[
                ParameterSpec(
                    name="operation",
                    data_type="string",
                    default="exclamation",
                    description="Operation to perform",
                    constraints={"enum": ["exclamation", "brackets", "quotes"]}
                )
            ]
        )
        super().__init__(spec)
        
    async def execute(self, context: ExecutionContext, node_data: NodeData) -> Dict[str, Any]:
        text = context.get_input_value(node_data.node_id, "text") or ""
        operation = node_data.parameters.get("operation", "exclamation")
        
        if operation == "exclamation":
            result = f"{text} !!!"
        elif operation == "brackets":
            result = f"[{text}]"
        elif operation == "quotes":
            result = f'"{text}"'
        else:
            result = text
            
        return {"result": result}