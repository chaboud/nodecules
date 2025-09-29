# Nodecules Architecture Documentation

## 🏗️ **System Overview**

Nodecules is a node-based graph processing engine with visual editing, conversational interfaces, and multi-LLM support. The system follows a layered architecture with clear separation between data models, execution engine, API layer, and frontend.

## 📦 **Core Components**

### **1. Data Models (`/core/types.py`)**

#### **Core Types**
- **`NodeSpec`** - Static specification of node type (inputs, outputs, parameters)
- **`NodeData`** - Runtime instance data (node_id, parameters, position)
- **`EdgeData`** - Connection between node ports
- **`GraphData`** - Complete graph with nodes and edges
- **`ExecutionContext`** - Runtime execution state and data flow

#### **Input Resolution Priority (Critical for API Usage)**
Input nodes resolve values in this order:
1. **By Label** - `execution_inputs[label]` (e.g., `"message": "Hello"`)
2. **By Ordinal** - `execution_inputs["input_1"]`, `"input_2"`, etc.
3. **By Node ID** - `execution_inputs[node_id]` (backwards compatibility)
4. **Parameter Default** - Node's configured default value

### **2. Execution Engine (`/core/executor.py`)**

#### **GraphExecutor**
- Manages graph execution lifecycle
- Supports both synchronous and streaming execution
- Uses topological sorting for node execution order
- Handles dependency resolution and error propagation

#### **Execution Flow**
1. **Parse Graph** - Convert API graph to internal `GraphData`
2. **Plan Execution** - Topological sort of nodes based on edges
3. **Execute Nodes** - Sequential execution with dependency resolution
4. **Collect Outputs** - Aggregate results from all output nodes

#### **Input/Output Resolution**
```python
# Input resolution in ExecutionContext.get_input_value()
for edge in self.graph.edges:
    if edge.target_node == node_id and edge.target_port == port_name:
        source_outputs = self.node_outputs.get(edge.source_node, {})
        return source_outputs.get(edge.source_port)
```

### **3. LLM Provider System (`/core/smart_context.py`)**

#### **Unified Provider Architecture**
- **`BaseProviderAdapter`** - Abstract base for all LLM providers
- **`OllamaAdapter`** - Local LLM support (stateless)
- **`AnthropicAdapter`** - Claude API support with API key auth
- **`BedrockAdapter`** - AWS Bedrock with bearer token or credential auth

#### **Context Supremacy Model**
Implements intelligent context handling for all LLM nodes:

**Priority Order:**
1. **Neither context_key nor context_data** → Fresh start
2. **context_key only** → Load from storage
3. **context_data only** → Use provided context directly  
4. **Both provided** → Apply supremacy rules:
   - Default: `context_key` dominant (faster)
   - With `context_data_dominant=true`: `context_data` dominant

### **4. Node System (`/plugins/`)**

#### **Built-in Nodes (`builtin_nodes.py`)**
- **`InputNode`** - Provides data to graph (supports multiple input methods)
- **`OutputNode`** - Collects results from graph
- **`TextTransformNode`** - String operations
- **`JSONExtractNode`** - JSON manipulation

#### **LLM Nodes**
- **`ImmutableChatNode`** - Content-addressable context storage
- **`SmartChatNode`** - Provider-native context management

#### **Node Registration**
```python
# Auto-discovery system loads nodes from:
# 1. Built-in nodes (builtin_nodes.py)  
# 2. Plugin files (*.py with NODE_TYPE attribute)
# 3. YAML-manifested plugins
```

### **5. API Layer (`/api/`)**

#### **Graph Management (`graphs.py`)**
- **`POST /api/v1/graphs/`** - Create graph
- **`GET /api/v1/graphs/`** - List graphs
- **`GET /api/v1/graphs/{id_or_name}`** - Get specific graph
- **`PUT /api/v1/graphs/{id_or_name}`** - Update graph

#### **Execution (`executions.py`)**
- **`POST /api/v1/executions/`** - Execute graph (sync)
- **`POST /api/v1/executions/stream`** - Execute graph (streaming)
- **`GET /api/v1/executions/{id}`** - Get execution results

#### **Plugin Discovery (`plugins.py`)**
- **`GET /api/v1/plugins/nodes`** - List available node types
- **`GET /api/v1/plugins/nodes/{type}`** - Get node specification

## 🔄 **Data Flow Architecture**

### **Graph Execution Sequence**

```
API Request → Graph Resolution → Node Planning → Sequential Execution → Result Aggregation
     ↓              ↓                ↓               ↓                    ↓
ExecutionRequest → GraphData → execution_order → node.execute() → context.node_outputs
```

### **Input Mapping Example**

**API Request:**
```json
{
  "graph_id": "test_graph",
  "inputs": {
    "0": "Hello Claude!",
    "message": "Alternative input"
  }
}
```

**Input Resolution:**
1. Node with `label="message"` gets `"Alternative input"`
2. First input node (no label) gets `"Hello Claude!"` via ordinal `"0"`
3. Remaining nodes use parameter defaults

### **LLM Provider Integration**

```
User Input → Input Node → LLM Chat Node → Provider Adapter → API Call → Response
    ↓           ↓             ↓               ↓              ↓          ↓
  "Hello" → {"output": "Hello"} → context_data → AnthropicAdapter → Claude API → "Hi there!"
```

## 🔧 **Key Design Patterns**

### **1. Context Supremacy Model**
Handles the complexity of different LLM provider context models:
- **Stateless providers** (Ollama) - Use full message history
- **Stateful providers** (Claude) - Use conversation IDs
- **Hybrid approach** - Support both context_key and context_data

### **2. Provider Abstraction**
All LLM providers implement the same interface:
```python
async def generate_with_context(
    self, context_data: Dict[str, Any], 
    new_message: str, **kwargs
) -> tuple[str, Dict[str, Any]]
```

### **3. Node Auto-Discovery**
Plugins are automatically discovered and registered:
- Python files with `NODE_TYPE` class attribute
- YAML manifests with entry points
- Built-in node registration

### **4. Streaming Support**
Graph execution supports real-time streaming:
- Server-Sent Events for node status
- Incremental output delivery
- Error propagation with context

## 🎯 **Critical Implementation Details**

### **Input Mapping Fix for Test Scripts**
The test script was failing because it used named inputs instead of ordinal inputs:

**❌ Wrong:**
```json
{"inputs": {"message": "Hello"}}
```

**✅ Correct:**
```json
{"inputs": {"0": "Hello"}}  // First input node
```

### **Environment Variable Loading**
Docker Compose requires restart to pick up `.env` changes:
```bash
docker-compose down && docker-compose up -d
```

### **Provider Authentication**
- **Claude:** `ANTHROPIC_API_KEY=sk-ant-api03-...`
- **Bedrock:** `AWS_BEARER_TOKEN_BEDROCK=...` (preferred) or AWS credentials
- **Ollama:** No authentication (local)

## 🔍 **Debugging Strategies**

### **Input Resolution Issues**
1. Check input node `label` parameter
2. Verify ordinal mapping (`"0"`, `"1"`, `"2"`)
3. Confirm execution_inputs format in API request

### **LLM Provider Issues**
1. Verify environment variables: `docker-compose exec backend env | grep ANTHROPIC`
2. Check provider availability: `/api/v1/plugins/nodes/{node_type}`
3. Review execution errors in response

### **Graph Execution Issues**
1. Check topological sort order
2. Verify edge connections (source_port → target_port)
3. Validate required inputs are satisfied

## 🚀 **Usage Patterns**

### **Simple Text Processing**
```
Input → Text Transform → Output
```

### **LLM Chat**
```
Input → LLM Chat → Output (response + context_data)
```

### **Context Continuation**
```
Input (message) + Input (context_data) → LLM Chat → Output
```

### **Multi-LLM Pipeline**
```
Input → LLM1 → JSON Extract → LLM2 → Output
```

This architecture provides a flexible, extensible foundation for node-based AI workflow processing with strong separation of concerns and clear data flow patterns.