# Nodecules API Usage Guide

This guide provides working examples for using the Nodecules API to create graphs, execute them, and work with the Claude/Anthropic integration.

## 🔗 **Base URL**
```
http://localhost:8000/api/v1
```

## 📊 **Key Endpoints**

### **Graphs**
- `GET /graphs/` - List all graphs  
- `POST /graphs/` - Create new graph
- `GET /graphs/{id_or_name}` - Get specific graph
- `PUT /graphs/{id_or_name}` - Update graph
- `DELETE /graphs/{id_or_name}` - Delete graph

### **Executions**
- `POST /executions/` - Execute graph (non-streaming)
- `POST /executions/stream` - Execute graph (streaming)
- `GET /executions/{id}` - Get execution results
- `GET /executions/` - List executions

### **Plugins/Nodes**
- `GET /plugins/nodes` - List available node types
- `GET /plugins/nodes/{type}` - Get specific node spec

## 🧪 **Working Examples**

### **1. List Available Node Types**
```bash
curl -X GET "http://localhost:8000/api/v1/plugins/nodes" | jq '.[].node_type'
```

**Expected Output:**
```json
[
  "input",
  "output", 
  "text_transform",
  "smart_chat",
  "immutable_chat",
  ...
]
```

### **2. Get Claude Chat Node Specification**
```bash
curl -X GET "http://localhost:8000/api/v1/plugins/nodes/immutable_chat" | jq
```

**Expected Output:**
```json
{
  "node_type": "immutable_chat",
  "display_name": "Immutable Chat",
  "description": "Chat with immutable, content-addressable context management",
  "category": "AI/Chat",
  "parameters": [
    {
      "name": "provider",
      "data_type": "select", 
      "default": "ollama",
      "description": "LLM provider",
      "constraints": {
        "options": ["ollama", "anthropic", "bedrock"]
      }
    }
  ]
}
```

### **3. Create a Simple Claude Test Graph**
```bash
curl -X POST "http://localhost:8000/api/v1/graphs/" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Claude API Test",
    "description": "Test Claude integration with context supremacy",
    "nodes": {
      "input_1": {
        "node_id": "input_1",
        "node_type": "input",
        "position": {"x": 100, "y": 100},
        "parameters": {
          "label": "message"
        }
      },
      "claude_chat": {
        "node_id": "claude_chat", 
        "node_type": "immutable_chat",
        "position": {"x": 300, "y": 100},
        "parameters": {
          "provider": "anthropic",
          "model": "claude-3-haiku-20240307",
          "system_prompt": "You are a helpful assistant. Always respond in exactly one sentence.",
          "temperature": 0.3,
          "streaming": false,
          "context_data_dominant": false
        }
      },
      "output_response": {
        "node_id": "output_response",
        "node_type": "output", 
        "position": {"x": 500, "y": 80},
        "parameters": {
          "label": "claude_response"
        }
      },
      "output_context": {
        "node_id": "output_context",
        "node_type": "output",
        "position": {"x": 500, "y": 140}, 
        "parameters": {
          "label": "context_data"
        }
      }
    },
    "edges": [
      {
        "edge_id": "e1",
        "source_node": "input_1",
        "source_port": "value",
        "target_node": "claude_chat",
        "target_port": "message"
      },
      {
        "edge_id": "e2", 
        "source_node": "claude_chat",
        "source_port": "response",
        "target_node": "output_response",
        "target_port": "input"
      },
      {
        "edge_id": "e3",
        "source_node": "claude_chat", 
        "source_port": "context_data",
        "target_node": "output_context",
        "target_port": "input"
      }
    ]
  }'
```

**Expected Response:**
```json
{
  "id": "12345678-1234-5678-9abc-123456789012",
  "name": "Claude API Test",
  "description": "Test Claude integration with context supremacy",
  "nodes": {...},
  "edges": [...],
  "created_at": "2025-01-15T10:30:00Z"
}
```

### **4. Execute the Claude Graph**
```bash
curl -X POST "http://localhost:8000/api/v1/executions/" \
  -H "Content-Type: application/json" \
  -d '{
    "graph_id": "Claude API Test",
    "inputs": {
      "message": "Hello Claude! Tell me about the weather."
    }
  }'
```

**Expected Response:**
```json
{
  "id": "87654321-4321-8765-dcba-210987654321",
  "graph_id": "12345678-1234-5678-9abc-123456789012", 
  "status": "completed",
  "inputs": {
    "message": "Hello Claude! Tell me about the weather."
  },
  "outputs": {
    "claude_response": "I'd be happy to tell you about the weather, but I don't have access to real-time weather data.",
    "context_data": {
      "messages": [
        {"role": "system", "content": "You are a helpful assistant..."},
        {"role": "user", "content": "Hello Claude! Tell me about the weather."},
        {"role": "assistant", "content": "I'd be happy to tell you about the weather..."}
      ],
      "provider": "anthropic",
      "model": "claude-3-haiku-20240307",
      "context_source": "fresh"
    }
  },
  "errors": {},
  "started_at": "2025-01-15T10:35:00Z",
  "completed_at": "2025-01-15T10:35:02Z"
}
```

### **5. Continue Conversation with Context**
```bash
# Create another graph execution using the context_data from previous response
curl -X POST "http://localhost:8000/api/v1/graphs/" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Claude Continuation Test",
    "description": "Test context continuation",
    "nodes": {
      "input_msg": {
        "node_id": "input_msg",
        "node_type": "input", 
        "position": {"x": 50, "y": 100},
        "parameters": {"label": "message"}
      },
      "input_context": {
        "node_id": "input_context",
        "node_type": "input",
        "position": {"x": 50, "y": 150}, 
        "parameters": {"label": "previous_context"}
      },
      "claude_chat": {
        "node_id": "claude_chat",
        "node_type": "immutable_chat",
        "position": {"x": 300, "y": 100},
        "parameters": {
          "provider": "anthropic",
          "model": "claude-3-haiku-20240307",
          "context_data_dominant": true
        }
      },
      "output_response": {
        "node_id": "output_response",
        "node_type": "output",
        "position": {"x": 500, "y": 100},
        "parameters": {"label": "response"}
      }
    },
    "edges": [
      {
        "edge_id": "e1",
        "source_node": "input_msg", 
        "source_port": "value",
        "target_node": "claude_chat",
        "target_port": "message"
      },
      {
        "edge_id": "e2",
        "source_node": "input_context",
        "source_port": "value", 
        "target_node": "claude_chat",
        "target_port": "context_data"
      },
      {
        "edge_id": "e3",
        "source_node": "claude_chat",
        "source_port": "response",
        "target_node": "output_response", 
        "target_port": "input"
      }
    ]
  }'

# Then execute with context from previous conversation
curl -X POST "http://localhost:8000/api/v1/executions/" \
  -H "Content-Type: application/json" \
  -d '{
    "graph_id": "Claude Continuation Test",
    "inputs": {
      "message": "What did I just ask you about?",
      "previous_context": {
        "messages": [
          {"role": "system", "content": "You are a helpful assistant. Always respond in exactly one sentence."},
          {"role": "user", "content": "Hello Claude! Tell me about the weather."},
          {"role": "assistant", "content": "I'"'"'d be happy to tell you about the weather, but I don'"'"'t have access to real-time weather data."}
        ],
        "provider": "anthropic"
      }
    }
  }'
```

### **6. Streaming Execution**
```bash
curl -X POST "http://localhost:8000/api/v1/executions/stream" \
  -H "Content-Type: application/json" \
  -d '{
    "graph_id": "Claude API Test", 
    "inputs": {
      "message": "Write a short poem about programming."
    }
  }'
```

**Expected Output (Server-Sent Events):**
```
data: {"type": "node_start", "node_id": "input_1", "timestamp": "2025-01-15T10:40:00Z"}

data: {"type": "node_complete", "node_id": "input_1", "outputs": {"value": "Write a short poem about programming."}}

data: {"type": "node_start", "node_id": "claude_chat", "timestamp": "2025-01-15T10:40:01Z"}

data: {"type": "node_complete", "node_id": "claude_chat", "outputs": {"response": "Code flows like poetry through logical minds, creating digital art one line at a time."}}

data: {"type": "execution_complete", "outputs": {...}}
```

## 🔧 **Testing Claude Integration**

### **Prerequisites**
1. Add your Claude API key to `.env`:
   ```bash
   ANTHROPIC_API_KEY=sk-ant-api03-your-key-here
   ```

2. Restart the backend:
   ```bash
   docker-compose restart backend
   ```

### **Quick Test Commands**
```bash
# 1. Verify backend is running
curl -X GET "http://localhost:8000/" | jq

# 2. Check available providers  
curl -X GET "http://localhost:8000/api/v1/plugins/nodes/immutable_chat" | jq '.parameters[] | select(.name=="provider")'

# 3. List existing graphs
curl -X GET "http://localhost:8000/api/v1/graphs/" | jq '.[].name'

# 4. Create and execute test graph (use examples above)
```

## 🎯 **Context Supremacy Model Testing**

The context supremacy model allows you to:

### **Scenario 1: No Context (Fresh Start)**
```json
{
  "inputs": {
    "message": "Hello!"
    // No context_key or context_data
  }
}
```

### **Scenario 2: Context Key Only**  
```json
{
  "inputs": {
    "message": "Continue conversation",
    "context_key": "abc123def456"
  }
}
```

### **Scenario 3: Context Data Only**
```json
{
  "inputs": {
    "message": "Continue with this context",
    "context_data": {
      "messages": [...],
      "provider": "anthropic"
    }
  }
}
```

### **Scenario 4: Both Provided (Test Supremacy)**
```json
{
  "inputs": {
    "message": "Test supremacy logic",
    "context_key": "abc123",
    "context_data": {...}
  }
}
```
- **Default**: `context_key` takes precedence
- **With `context_data_dominant: true`**: `context_data` takes precedence

## 🚨 **Common Issues**

### **Authentication Errors**
```json
{
  "error": "Anthropic API error: authentication failed"
}
```
**Solution**: Check your `ANTHROPIC_API_KEY` in `.env`

### **Model Not Found**
```json
{
  "error": "Bedrock API error: model not found"
}
```
**Solution**: Use correct model IDs like `claude-3-haiku-20240307`

### **Graph Not Found**
```json
{
  "detail": "Graph not found: My Graph"
}
```
**Solution**: Use exact graph name or UUID from `GET /graphs/`

## 📱 **Frontend Integration**

The frontend automatically uses these APIs via the service layer:
- Graph creation/editing uses the graph management endpoints
- Chat interface uses streaming execution
- Debug views use the context_data outputs for inspection

Access the web interface at: **http://localhost:3000**