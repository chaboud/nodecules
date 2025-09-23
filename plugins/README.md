# Nodecules Plugin System

This directory contains custom plugins for Nodecules. The system supports two types of plugins:

## 📁 Plugin Types

### 1. **Auto-Discovery Plugins** (Recommended for simple plugins)
- Drop any `.py` file containing `BaseNode` subclasses directly into this directory
- No configuration required - nodes are automatically discovered and loaded
- Perfect for quick custom nodes

### 2. **YAML-Based Plugins** (For complex plugins)
- Create a subdirectory with a `plugin.yaml` manifest file
- More control over metadata, dependencies, and organization
- Better for distributable plugins

## 🚀 Quick Start - Auto-Discovery

1. **Create a Python file** in this directory (e.g., `my_custom_nodes.py`)
2. **Import the base class**:
   ```python
   from nodecules.core.types import BaseNode, NodeSpec, PortSpec, DataType, ExecutionContext, NodeData
   ```
3. **Create your node class**:
   ```python
   class MyCustomNode(BaseNode):
       NODE_TYPE = "my_custom"
       
       def __init__(self):
           spec = NodeSpec(
               node_type=self.NODE_TYPE,
               display_name="My Custom Node",
               description="Does something awesome",
               category="Custom",
               inputs=[PortSpec(name="input", data_type=DataType.TEXT)],
               outputs=[PortSpec(name="output", data_type=DataType.TEXT)]
           )
           super().__init__(spec)
           
       async def execute(self, context: ExecutionContext, node_data: NodeData):
           input_value = context.get_input_value(node_data.node_id, "input")
           result = f"Processed: {input_value}"
           return {"output": result}
   ```
4. **Restart the backend** - your node will be automatically discovered and available!

## 📋 Node Specification

### Required Components

- **NODE_TYPE**: Unique identifier for your node type
- **NodeSpec**: Defines the node's interface (inputs, outputs, parameters)
- **execute method**: The main processing logic

### Common Port Types

- `DataType.TEXT` - Plain text strings
- `DataType.JSON` - JSON objects and dictionaries
- `DataType.ANY` - Any data type
- `DataType.NUMBER` - Numeric values

### Example Node Categories

- `"Input/Output"` - Data input/output nodes
- `"Text Processing"` - Text manipulation nodes
- `"Data Processing"` - Data transformation nodes
- `"AI"` - AI and machine learning nodes
- `"Custom"` - Your custom nodes

## 🔧 Advanced Features

### Parameters
Add configurable parameters to your nodes:

```python
parameters=[
    ParameterSpec(
        name="operation",
        data_type="string",
        default="uppercase",
        description="Operation to perform",
        constraints={"enum": ["uppercase", "lowercase", "title"]}
    )
]
```

### Multiple Outputs
Nodes can have multiple output ports:

```python
outputs=[
    PortSpec(name="result", data_type=DataType.TEXT),
    PortSpec(name="count", data_type=DataType.NUMBER),
    PortSpec(name="metadata", data_type=DataType.JSON)
]
```

### Context Access
Access execution context and inputs:

```python
async def execute(self, context: ExecutionContext, node_data: NodeData):
    # Get input from connected nodes
    input_text = context.get_input_value(node_data.node_id, "input")
    
    # Get node parameters
    operation = node_data.parameters.get("operation", "default")
    
    # Access execution-level inputs (for input nodes)
    if "user_input" in context.execution_inputs:
        user_data = context.execution_inputs["user_input"]
    
    return {"result": processed_value}
```

## 🗂️ YAML-Based Plugin Structure

For more complex plugins, create a subdirectory with this structure:

```
plugins/
├── my_plugin/
│   ├── plugin.yaml
│   ├── __init__.py
│   └── nodes.py
```

**plugin.yaml**:
```yaml
name: my_plugin
version: "1.0.0"
author: "Your Name"
description: "Description of your plugin"
entry_point: "nodes.py"
node_types:
  - "my_node_type"
dependencies:
  - "requests"
  - "numpy"
```

## 📚 Examples

Check `example_custom_node.py` in this directory for working examples of:
- Text processing nodes
- Multiple node types in one file
- Parameter handling
- Multiple outputs

## 🔄 Loading Process

1. **YAML-based plugins** are loaded first
2. **Auto-discovered plugins** are loaded second
3. Node type conflicts show warnings in logs
4. All loaded nodes appear in the UI palette and API

## 🧪 Testing Your Plugins

After adding a plugin, restart the backend to load it:
```bash
docker-compose restart backend
```

### Verify Plugin Discovery
Check that your plugins were loaded:
```bash
# Check logs for plugin loading
docker-compose logs backend | grep plugin

# List all available nodes (should include your custom types)
curl -s "http://localhost:8000/api/v1/plugins/nodes" | jq '.[] | select(.node_type | contains("example"))'
```

### Quick Test with Web UI
1. Go to http://localhost:3000
2. Create a new graph
3. Your custom node types should appear in the node palette
4. Drag and connect them to build a test workflow

### API Test Example
```bash
# Test the example_processor node
curl -X POST "http://localhost:8000/api/v1/executions/" \
  -H "Content-Type: application/json" \
  -d '{
    "graph_name": "test",
    "nodes": {
      "input": {"node_type": "input", "parameters": {"value": "Hello World"}},
      "processor": {"node_type": "example_processor", "parameters": {"processing_type": "word_count"}},
      "output": {"node_type": "output"}
    },
    "edges": [
      {"source": "input", "target": "processor", "source_port": "output", "target_port": "text"},
      {"source": "processor", "target": "output", "source_port": "count_string", "target_port": "input"}
    ]
  }'
```

## 🛠️ Development Tips

1. **Restart the backend** after adding/modifying plugins  
2. **Check the logs** for loading status and errors
3. **Use unique NODE_TYPE** values to avoid conflicts
4. **Test with simple examples** before building complex nodes
5. **Add proper error handling** in your execute methods

### Working Examples
Check `example_custom_node.py` in this directory for working examples of:
- Text processing nodes (`example_processor`)
- Text formatting nodes (`simple_text_processor`) 
- Multiple node types in one file
- Parameter handling with constraints
- Multiple outputs with different data types

## 🐛 Troubleshooting

- **Node not appearing?** Check backend logs for import errors
- **Import errors?** Make sure all dependencies are installed in the backend container
- **Type conflicts?** Use unique NODE_TYPE values
- **Runtime errors?** Add try/catch blocks in your execute method

## 📖 API Integration

Your custom nodes automatically become available via:

- **Web UI**: Node palette for visual graph building
- **REST API**: `GET /api/v1/plugins/nodes` lists all available nodes
- **Graph Execution**: Use your node_type in graph JSON definitions