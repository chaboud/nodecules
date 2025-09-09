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
                ),
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
        text1 = context.get_input_value(node_data.node_id, "text1")
        text2 = context.get_input_value(node_data.node_id, "text2")  
        text3 = context.get_input_value(node_data.node_id, "text3")
        separator = node_data.parameters.get("separator", " ")
        
        # Simple type conversion - let Python handle most conversions naturally
        def to_string(value):
            if value is None or value == "":
                return ""
            else:
                return str(value)
        
        # Convert and filter out empty texts
        texts = [to_string(t) for t in [text1, text2, text3] if to_string(t)]
        result = separator.join(texts)
        
        return {"output": result}


class JsonExtractNode(BaseNode):
    """Extract a field value from JSON/dict data."""
    
    NODE_TYPE = "json_extract"
    
    def __init__(self):
        spec = NodeSpec(
            node_type=self.NODE_TYPE,
            display_name="JSON Extract",
            description="Extract a specific field value from JSON/dict data",
            category="Data Processing",
            inputs=[
                PortSpec(name="data", data_type=DataType.JSON, description="JSON object or dict"),
                PortSpec(name="key", data_type=DataType.TEXT, description="Key to extract", required=False)
            ],
            outputs=[
                PortSpec(name="value", data_type=DataType.TEXT, description="Extracted value as string"),
                PortSpec(name="found", data_type=DataType.JSON, description="Whether key was found")
            ],
            parameters=[
                ParameterSpec(
                    name="key_path",
                    data_type="string",
                    default="",
                    description="Dot-separated path to extract (e.g., 'user.name' or 'words')"
                ),
                ParameterSpec(
                    name="default_value",
                    data_type="string", 
                    default="",
                    description="Default value if key not found"
                ),
                ParameterSpec(
                    name="stringify",
                    data_type="boolean",
                    default=True,
                    description="Convert extracted value to string"
                )
            ]
        )
        super().__init__(spec)
        
    async def execute(self, context: ExecutionContext, node_data: NodeData) -> Dict[str, Any]:
        data = context.get_input_value(node_data.node_id, "data")
        key_input = context.get_input_value(node_data.node_id, "key")
        
        # Use input key if provided, otherwise parameter
        key_path = key_input or node_data.parameters.get("key_path", "")
        default_value = node_data.parameters.get("default_value", "")
        stringify = node_data.parameters.get("stringify", True)
        
        if not data or not key_path:
            return {
                "value": default_value,
                "found": {"success": False, "reason": "missing_data_or_key"}
            }
        
        try:
            # Handle dot-separated paths like "user.name" 
            current_data = data
            for key_part in key_path.split('.'):
                if isinstance(current_data, dict) and key_part in current_data:
                    current_data = current_data[key_part]
                else:
                    return {
                        "value": default_value,
                        "found": {"success": False, "reason": f"key_not_found: {key_part}"}
                    }
            
            # Convert to string if requested
            if stringify:
                value = str(current_data)
            else:
                value = current_data
                
            return {
                "value": value,
                "found": {"success": True, "extracted": current_data}
            }
            
        except Exception as e:
            return {
                "value": default_value,
                "found": {"success": False, "reason": f"error: {str(e)}"}
            }


class JsonReplaceNode(BaseNode):
    """Replace a field value in JSON/dict data."""
    
    NODE_TYPE = "json_replace"
    
    def __init__(self):
        spec = NodeSpec(
            node_type=self.NODE_TYPE,
            display_name="JSON Replace",
            description="Replace a specific field value in JSON/dict data",
            category="Data Processing",
            inputs=[
                PortSpec(name="data", data_type=DataType.JSON, description="JSON object or dict to modify"),
                PortSpec(name="key", data_type=DataType.TEXT, description="Key to replace", required=False),
                PortSpec(name="value", data_type=DataType.ANY, description="New value to set")
            ],
            outputs=[
                PortSpec(name="result", data_type=DataType.JSON, description="Modified JSON object"),
                PortSpec(name="success", data_type=DataType.JSON, description="Whether replacement was successful")
            ],
            parameters=[
                ParameterSpec(
                    name="key_path",
                    data_type="string",
                    default="",
                    description="Dot-separated path to replace (e.g., 'user.name' or 'content')"
                ),
                ParameterSpec(
                    name="create_if_missing",
                    data_type="boolean",
                    default=True,
                    description="Create the key path if it doesn't exist"
                )
            ]
        )
        super().__init__(spec)
        
    async def execute(self, context: ExecutionContext, node_data: NodeData) -> Dict[str, Any]:
        data = context.get_input_value(node_data.node_id, "data")
        key_input = context.get_input_value(node_data.node_id, "key")
        new_value = context.get_input_value(node_data.node_id, "value")
        
        # Use input key if provided, otherwise parameter
        key_path = key_input or node_data.parameters.get("key_path", "")
        create_if_missing = node_data.parameters.get("create_if_missing", True)
        
        if not data or not key_path:
            return {
                "result": data,
                "success": {"success": False, "reason": "missing_data_or_key"}
            }
        
        try:
            # Deep copy the data to avoid modifying original
            import copy
            result_data = copy.deepcopy(data)
            
            # Handle dot-separated paths like "user.name"
            path_parts = key_path.split('.')
            current_data = result_data
            
            # Navigate to the parent of the target key
            for i, key_part in enumerate(path_parts[:-1]):
                if isinstance(current_data, dict):
                    if key_part not in current_data:
                        if create_if_missing:
                            current_data[key_part] = {}
                        else:
                            return {
                                "result": data,
                                "success": {"success": False, "reason": f"path_not_found: {key_part}"}
                            }
                    current_data = current_data[key_part]
                else:
                    return {
                        "result": data,
                        "success": {"success": False, "reason": f"invalid_path_type: {key_part}"}
                    }
            
            # Set the final key
            final_key = path_parts[-1]
            if isinstance(current_data, dict):
                current_data[final_key] = new_value
                return {
                    "result": result_data,
                    "success": {"success": True, "replaced": key_path, "value": new_value}
                }
            else:
                return {
                    "result": data,
                    "success": {"success": False, "reason": "target_not_dict"}
                }
                
        except Exception as e:
            return {
                "result": data,
                "success": {"success": False, "reason": f"error: {str(e)}"}
            }


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
                ),
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


# Import core chat nodes  
from .smart_chat_node import SMART_CHAT_NODES
from .immutable_chat_node import IMMUTABLE_CHAT_NODES
from .context_nodes import CONTEXT_NODES
# Import specialized nodes
from .graph_nodes import GRAPH_NODES

# Registry of built-in nodes
BUILTIN_NODES = {
    "input": InputNode,
    "text_transform": TextTransformNode,
    "text_filter": TextFilterNode,
    "text_concat": TextConcatNode,
    "json_extract": JsonExtractNode,
    "json_replace": JsonReplaceNode,
    "output": OutputNode,
    **SMART_CHAT_NODES,  # Smart context-aware chat
    **IMMUTABLE_CHAT_NODES,  # Immutable content-addressable chat
    **CONTEXT_NODES,  # Context storage and key generation
    **GRAPH_NODES,  # Graph-as-node functionality
}