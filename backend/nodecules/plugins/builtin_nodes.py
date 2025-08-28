"""Built-in node types for basic operations."""

import json
import re
from typing import Any, Dict

from ..core.types import BaseNode, DataType, NodeSpec, ParameterSpec, PortSpec, ResourceRequirement, ExecutionContext, NodeData


class InputNode(BaseNode):
    """Input node for providing data to the graph."""
    
    NODE_TYPE = "input"
    
    def __init__(self):
        spec = NodeSpec(
            node_type=self.NODE_TYPE,
            display_name="Input",
            description="Provides input data to the graph",
            category="Input/Output",
            inputs=[],
            outputs=[
                PortSpec(name="output", data_type=DataType.ANY, description="Input data")
            ],
            parameters=[
                ParameterSpec(
                    name="label",
                    data_type="string",
                    default="",
                    description="Friendly name for this input (e.g., 'source_text', 'temperature')"
                ),
                ParameterSpec(
                    name="value",
                    data_type="string",
                    default="",
                    description="Input value"
                ),
                ParameterSpec(
                    name="data_type",
                    data_type="string", 
                    default="text",
                    description="Data type (text, json, number)"
                )
            ]
        )
        super().__init__(spec)
        
    async def execute(self, context: ExecutionContext, node_data: NodeData) -> Dict[str, Any]:
        # Try multiple input resolution methods in order of priority
        value = None
        
        # 1. Try by label (if specified)
        label = node_data.parameters.get("label", "").strip()
        if label and label in context.execution_inputs:
            value = context.execution_inputs[label]
        
        # 2. Try by ordinal (input_1, input_2, etc.)
        if value is None:
            # Find this node's ordinal position among input nodes
            input_nodes = [node_id for node_id, node in context.graph.nodes.items() 
                          if node.node_type == "input"]
            input_nodes.sort()  # Ensure consistent ordering
            
            try:
                ordinal = input_nodes.index(node_data.node_id) + 1  # 1-based indexing
                ordinal_key = f"input_{ordinal}"
                if ordinal_key in context.execution_inputs:
                    value = context.execution_inputs[ordinal_key]
            except ValueError:
                pass  # Node not found in input_nodes (shouldn't happen)
        
        # 3. Try by node ID (backwards compatibility)
        if value is None and node_data.node_id in context.execution_inputs:
            value = context.execution_inputs[node_data.node_id]
        
        # 4. Fall back to parameter value
        if value is None:
            value = node_data.parameters.get("value", "")
            
        data_type = node_data.parameters.get("data_type", "text")
        
        # Convert value based on data type
        if data_type == "json" and isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                pass  # Keep as string if invalid JSON
        elif data_type == "number":
            try:
                value = float(value) if '.' in str(value) else int(value)
            except (ValueError, TypeError):
                value = 0
                
        return {"output": value}


class TextTransformNode(BaseNode):
    """Transform text using various operations."""
    
    NODE_TYPE = "text_transform"
    
    def __init__(self):
        spec = NodeSpec(
            node_type=self.NODE_TYPE,
            display_name="Text Transform",
            description="Transform text using various operations",
            category="Text Processing",
            inputs=[
                PortSpec(name="text", data_type=DataType.TEXT, description="Input text")
            ],
            outputs=[
                PortSpec(name="output", data_type=DataType.TEXT, description="Transformed text")
            ],
            parameters=[
                ParameterSpec(
                    name="operation",
                    data_type="string",
                    default="uppercase",
                    description="Transform operation",
                    constraints={"enum": ["uppercase", "lowercase", "title", "strip", "reverse"]}
                )
            ]
        )
        super().__init__(spec)
        
    async def execute(self, context: ExecutionContext, node_data: NodeData) -> Dict[str, Any]:
        text = context.get_input_value(node_data.node_id, "text") or ""
        operation = node_data.parameters.get("operation", "uppercase")
        
        if operation == "uppercase":
            result = text.upper()
        elif operation == "lowercase":
            result = text.lower()
        elif operation == "title":
            result = text.title()
        elif operation == "strip":
            result = text.strip()
        elif operation == "reverse":
            result = text[::-1]
        else:
            result = text
            
        return {"output": result}


class TextFilterNode(BaseNode):
    """Filter text using regex or string matching."""
    
    NODE_TYPE = "text_filter"
    
    def __init__(self):
        spec = NodeSpec(
            node_type=self.NODE_TYPE,
            display_name="Text Filter",
            description="Filter text using regex or string patterns",
            category="Text Processing",
            inputs=[
                PortSpec(name="text", data_type=DataType.TEXT, description="Input text")
            ],
            outputs=[
                PortSpec(name="matches", data_type=DataType.TEXT, description="Matching text"),
                PortSpec(name="filtered", data_type=DataType.TEXT, description="Text with matches removed")
            ],
            parameters=[
                ParameterSpec(
                    name="pattern",
                    data_type="string",
                    default="",
                    description="Regex pattern or string to match"
                ),
                ParameterSpec(
                    name="use_regex",
                    data_type="boolean",
                    default=True,
                    description="Use regex pattern matching"
                )
            ]
        )
        super().__init__(spec)
        
    async def execute(self, context: ExecutionContext, node_data: NodeData) -> Dict[str, Any]:
        text = context.get_input_value(node_data.node_id, "text") or ""
        pattern = node_data.parameters.get("pattern", "")
        use_regex = node_data.parameters.get("use_regex", True)
        
        if not pattern:
            return {"matches": "", "filtered": text}
            
        if use_regex:
            try:
                matches = re.findall(pattern, text)
                filtered = re.sub(pattern, "", text)
                return {
                    "matches": "\n".join(matches),
                    "filtered": filtered
                }
            except re.error:
                # Invalid regex, fall back to string matching
                use_regex = False
                
        if not use_regex:
            # Simple string matching
            if pattern in text:
                matches = pattern
                filtered = text.replace(pattern, "")
            else:
                matches = ""
                filtered = text
                
            return {"matches": matches, "filtered": filtered}


class TextConcatNode(BaseNode):
    """Concatenate multiple text inputs."""
    
    NODE_TYPE = "text_concat"
    
    def __init__(self):
        spec = NodeSpec(
            node_type=self.NODE_TYPE,
            display_name="Text Concat",
            description="Concatenate multiple text inputs",
            category="Text Processing",
            inputs=[
                PortSpec(name="text1", data_type=DataType.TEXT, description="First text input"),
                PortSpec(name="text2", data_type=DataType.TEXT, description="Second text input", required=False),
                PortSpec(name="text3", data_type=DataType.TEXT, description="Third text input", required=False)
            ],
            outputs=[
                PortSpec(name="output", data_type=DataType.TEXT, description="Concatenated text")
            ],
            parameters=[
                ParameterSpec(
                    name="separator",
                    data_type="string",
                    default=" ",
                    description="Separator between texts"
                )
            ]
        )
        super().__init__(spec)
        
    async def execute(self, context: ExecutionContext, node_data: NodeData) -> Dict[str, Any]:
        text1 = context.get_input_value(node_data.node_id, "text1") or ""
        text2 = context.get_input_value(node_data.node_id, "text2") or ""
        text3 = context.get_input_value(node_data.node_id, "text3") or ""
        separator = node_data.parameters.get("separator", " ")
        
        # Filter out empty texts
        texts = [t for t in [text1, text2, text3] if t]
        result = separator.join(texts)
        
        return {"output": result}


class OutputNode(BaseNode):
    """Output node for displaying results."""
    
    NODE_TYPE = "output"
    
    def __init__(self):
        spec = NodeSpec(
            node_type=self.NODE_TYPE,
            display_name="Output",
            description="Display output from the graph",
            category="Input/Output",
            inputs=[
                PortSpec(name="input", data_type=DataType.ANY, description="Data to output")
            ],
            outputs=[
                PortSpec(name="result", data_type=DataType.ANY, description="Output result for capture")
            ],
            parameters=[
                ParameterSpec(
                    name="label",
                    data_type="string",
                    default="Output",
                    description="Label for the output"
                )
            ]
        )
        super().__init__(spec)
        
    async def execute(self, context: ExecutionContext, node_data: NodeData) -> Dict[str, Any]:
        input_value = context.get_input_value(node_data.node_id, "input")
        label = node_data.parameters.get("label", "Output")
        
        # Log the output (for debugging/monitoring)
        print(f"{label}: {input_value}")
        
        # Return the input value as output so it appears in execution results
        return {
            "result": input_value,
            "label": label
        }


# Registry of built-in nodes
BUILTIN_NODES = {
    "input": InputNode,
    "text_transform": TextTransformNode,
    "text_filter": TextFilterNode,
    "text_concat": TextConcatNode,
    "output": OutputNode
}