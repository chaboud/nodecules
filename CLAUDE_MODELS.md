# Claude Model Usage Guide

## 🤖 **Available Claude Models**

### **Currently Available Models**

#### **Claude 3 Haiku (Working)**
- **Model ID**: `claude-3-haiku-20240307`
- **Best for**: Fast responses, simple tasks, high-volume usage
- **Strengths**: Speed, cost-effectiveness, reliable availability
- **Speed**: Very fast
- **Cost**: Lowest
- **Status**: ✅ Confirmed working

#### **Claude 3.5 Sonnet (May Require Higher Tier)**
- **Model ID**: `claude-3-5-sonnet-20240620`
- **Best for**: Complex reasoning and analysis
- **Strengths**: Excellent reasoning, coding, analysis
- **Speed**: Moderate
- **Cost**: Mid-range
- **Status**: ⚠️ May require API tier upgrade

#### **Claude 3 Opus (Requires Higher Tier)**
- **Model ID**: `claude-3-opus-20240229`
- **Best for**: Most complex reasoning tasks
- **Strengths**: Highest capability
- **Speed**: Slower
- **Cost**: Highest
- **Status**: ⚠️ Requires higher API tier

### **Previous Versions**

#### **Claude 3.5 Sonnet (Previous)**
- **Model ID**: `claude-3-5-sonnet-20240620`
- **Status**: Still supported, but prefer latest version

#### **Claude 3 Legacy Models**
- **Claude 3 Haiku**: `claude-3-haiku-20240307`
- **Claude 3 Sonnet**: `claude-3-sonnet-20240229`  
- **Claude 3 Opus**: `claude-3-opus-20240229` (most capable, expensive)

## 🎯 **How to Use Different Models**

### **Method 1: Web Interface**
1. Go to http://localhost:3000
2. Create an `immutable_chat` or `smart_chat` node
3. Set **Provider**: `anthropic`
4. Set **Model**: `claude-3-5-sonnet-20241022` (or your preferred model)

### **Method 2: API (Graph Creation)**
```bash
curl -X POST "http://localhost:8000/api/v1/graphs/" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Claude 3.5 Sonnet Test",
    "nodes": {
      "input_1": {
        "node_id": "input_1",
        "node_type": "input",
        "position": {"x": 100, "y": 100},
        "parameters": {"label": "message"}
      },
      "claude_chat": {
        "node_id": "claude_chat",
        "node_type": "immutable_chat",
        "position": {"x": 300, "y": 100},
        "parameters": {
          "provider": "anthropic",
          "model": "claude-3-5-sonnet-20241022",
          "system_prompt": "You are an expert assistant.",
          "temperature": 0.7
        }
      }
    },
    "edges": [...]
  }'
```

### **Method 3: Update Existing Graph**
```bash
curl -X PUT "http://localhost:8000/api/v1/graphs/your-graph-id" \
  -H "Content-Type: application/json" \
  -d '{
    "nodes": {
      "your_chat_node": {
        "parameters": {
          "model": "claude-3-5-haiku-20241022"
        }
      }
    }
  }'
```

## 🔧 **Model Selection Guide**

### **For Fast, Simple Tasks → Claude 3.5 Haiku**
```json
{
  "provider": "anthropic",
  "model": "claude-3-5-haiku-20241022",
  "temperature": 0.3
}
```
**Use cases**: 
- Simple Q&A
- Text classification
- Quick summaries
- High-volume processing

### **For Complex Reasoning → Claude 3.5 Sonnet**
```json
{
  "provider": "anthropic", 
  "model": "claude-3-5-sonnet-20241022",
  "temperature": 0.7
}
```
**Use cases**:
- Code generation/analysis
- Complex reasoning
- Research and analysis
- Creative writing

### **For Maximum Capability → Claude 3 Opus**
```json
{
  "provider": "anthropic",
  "model": "claude-3-opus-20240229", 
  "temperature": 0.5
}
```
**Use cases**:
- Most complex reasoning
- Highest quality outputs
- Research-grade analysis
- When cost is not a concern

## 🧪 **Quick Test Examples**

### **Test Claude 3 Haiku (Confirmed Working)**
```bash
# Create a Haiku test graph
curl -X POST "http://localhost:8000/api/v1/graphs/" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Claude Haiku Test",
    "nodes": {
      "input_1": {"node_id": "input_1", "node_type": "input", "parameters": {"label": "message"}},
      "haiku_chat": {"node_id": "haiku_chat", "node_type": "immutable_chat", "parameters": {"provider": "anthropic", "model": "claude-3-haiku-20240307", "temperature": 0.7}}
    },
    "edges": [{"edge_id": "e1", "source_node": "input_1", "source_port": "output", "target_node": "haiku_chat", "target_port": "message"}]
  }'

# Execute the test
curl -X POST "http://localhost:8000/api/v1/executions/" \
  -H "Content-Type: application/json" \
  -d '{
    "graph_id": "Claude Haiku Test",
    "inputs": {"message": "What is machine learning?"}
  }'
```

### **Test Model Availability**
```bash
# Quick test to check if a model works
curl -X POST "http://localhost:8000/api/v1/executions/" \
  -H "Content-Type: application/json" \
  -d '{
    "graph_id": "Quick Claude Test",
    "inputs": {"message": "Hello, which model are you?"}
  }'
```

## 🔄 **Switching Models in Existing Graphs**

### **Option 1: Parameter Override (Runtime)**
You can override the model at execution time by connecting a model input:

1. Add a `model` input port to your chat node
2. Connect an input node with the model ID
3. Execute with different models without changing the graph

### **Option 2: Graph Update (Persistent)**
Update the graph definition to permanently change the model:

```bash
# Update graph to use latest Sonnet
curl -X PUT "http://localhost:8000/api/v1/graphs/your-graph-name" \
  -H "Content-Type: application/json" \
  -d '{
    "nodes": {
      "your_chat_node_id": {
        "parameters": {
          "model": "claude-3-5-sonnet-20241022"
        }
      }
    }
  }'
```

## 💡 **Best Practices**

### **Model Selection Strategy**
1. **Start with Haiku** for prototyping (fast, cheap)
2. **Use Sonnet** for production (balanced)
3. **Use Opus** only when you need maximum capability

### **Cost Optimization**
- Use **Haiku** for simple preprocessing steps
- Use **Sonnet** for main reasoning tasks
- Chain models: Haiku → Sonnet → Output

### **Performance Tuning**
- **Lower temperature (0.1-0.3)** for factual tasks
- **Higher temperature (0.7-1.0)** for creative tasks
- **Adjust max_tokens** based on expected response length

## 🔍 **Troubleshooting**

### **Model Not Found Error**
```json
{"error": "Model not found: claude-3-5-sonnet-20241022"}
```
**Solutions**:
1. Verify the exact model ID spelling
2. Check if your API key has access to the model
3. Try a legacy model like `claude-3-haiku-20240307`

### **Rate Limits**
- **Haiku**: Higher rate limits
- **Sonnet**: Moderate rate limits  
- **Opus**: Lower rate limits

Use Haiku for high-volume testing and Sonnet/Opus for production.

## 📊 **Model Comparison**

| Model | Speed | Cost | Quality | Best For |
|-------|-------|------|---------|----------|
| **Claude 3.5 Haiku** | ⚡ Very Fast | 💰 Cheapest | ⭐⭐⭐ Good | Simple tasks, high volume |
| **Claude 3.5 Sonnet** | ⚡ Fast | 💰💰 Moderate | ⭐⭐⭐⭐ Excellent | Most use cases |
| **Claude 3 Opus** | ⚡ Slower | 💰💰💰 Expensive | ⭐⭐⭐⭐⭐ Best | Complex reasoning |

Choose based on your specific needs for speed, cost, and quality!