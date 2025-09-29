# AWS Bedrock Model Usage Guide

## 🚀 **Available AWS Bedrock Models**

### **Anthropic Claude Models on Bedrock**

#### **Claude 3.5 Haiku (Latest - Recommended for Speed)**
- **Model ID**: `us.anthropic.claude-3-5-haiku-20241022-v1:0`
- **Best for**: Fast responses, simple tasks, high-volume usage
- **Strengths**: Fastest Claude model, cost-effective, reliable
- **Speed**: Very fast
- **Cost**: Lowest
- **Status**: ✅ Latest version

#### **Claude 3.7 Sonnet (Latest - Recommended for Quality)**
- **Model ID**: `us.anthropic.claude-3-7-sonnet-20250219-v1:0`
- **Best for**: Complex reasoning and analysis, coding tasks
- **Strengths**: Excellent reasoning, coding, analysis, balanced performance
- **Speed**: Moderate
- **Cost**: Mid-range
- **Status**: ✅ Latest version, confirmed working

#### **Claude 3.5 Sonnet (Previous)**
- **Model ID**: `us.anthropic.claude-3-5-sonnet-20241022-v1:0`
- **Best for**: Complex reasoning and analysis, coding tasks
- **Strengths**: Excellent reasoning, coding, analysis, balanced performance
- **Speed**: Moderate
- **Cost**: Mid-range
- **Status**: ⚠️ May require specific permissions

#### **Claude 3 Haiku (Legacy)**
- **Model ID**: `anthropic.claude-3-haiku-20240307-v1:0`
- **Best for**: Legacy compatibility
- **Status**: ⚠️ Consider upgrading to latest version

#### **Claude 3 Sonnet (Legacy)**
- **Model ID**: `anthropic.claude-3-sonnet-20240229-v1:0`
- **Best for**: Legacy compatibility
- **Status**: ⚠️ Consider upgrading to latest version

#### **Claude 3 Opus (Most Capable)**
- **Model ID**: `anthropic.claude-3-opus-20240229-v1:0`
- **Best for**: Most complex reasoning tasks
- **Strengths**: Highest capability, best quality
- **Speed**: Slower
- **Cost**: Highest
- **Status**: ✅ Available

### **Other Bedrock Models**

#### **Meta Llama Models**
- **Llama 3.1 405B**: `meta.llama3-1-405b-instruct-v1:0`
- **Llama 3.1 70B**: `meta.llama3-1-70b-instruct-v1:0`  
- **Llama 3.1 8B**: `meta.llama3-1-8b-instruct-v1:0`

#### **Mistral AI Models**
- **Mistral Large 2**: `mistral.mistral-large-2402-v1:0`
- **Mistral 7B**: `mistral.mistral-7b-instruct-v0:2`

#### **Amazon Titan Models**
- **Titan Text G1 Express**: `amazon.titan-text-express-v1`
- **Titan Text G1 Lite**: `amazon.titan-text-lite-v1`

## 🎯 **How to Use Bedrock Models**

### **Method 1: Web Interface**
1. Go to http://localhost:3000
2. Create an `immutable_chat` or `smart_chat` node
3. Set **Provider**: `bedrock`
4. Set **Model**: `us.anthropic.claude-3-5-sonnet-20241022-v1:0` (or your preferred model)

### **Method 2: API (Graph Creation)**
```bash
curl -X POST "http://localhost:8000/api/v1/graphs/" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Bedrock Claude Test",
    "nodes": {
      "input_1": {
        "node_id": "input_1",
        "node_type": "input",
        "position": {"x": 100, "y": 100},
        "parameters": {"label": "message"}
      },
      "bedrock_chat": {
        "node_id": "bedrock_chat",
        "node_type": "immutable_chat",
        "position": {"x": 300, "y": 100},
        "parameters": {
          "provider": "bedrock",
          "model": "us.anthropic.claude-3-5-sonnet-20241022-v1:0",
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
          "model": "us.anthropic.claude-3-5-haiku-20241022-v1:0"
        }
      }
    }
  }'
```

## 🔧 **Model Selection Guide**

### **For Fast, Simple Tasks → Claude 3.5 Haiku**
```json
{
  "provider": "bedrock",
  "model": "us.anthropic.claude-3-5-haiku-20241022-v1:0",
  "temperature": 0.3
}
```
**Use cases**: 
- Simple Q&A
- Text classification
- Quick summaries
- High-volume processing

### **For Complex Reasoning → Claude 3.7 Sonnet**
```json
{
  "provider": "bedrock", 
  "model": "us.anthropic.claude-3-7-sonnet-20250219-v1:0",
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
  "provider": "bedrock",
  "model": "anthropic.claude-3-opus-20240229-v1:0", 
  "temperature": 0.5
}
```
**Use cases**:
- Most complex reasoning
- Highest quality outputs
- Research-grade analysis
- When cost is not a concern

### **For Open Source Models → Llama 3.1**
```json
{
  "provider": "bedrock",
  "model": "meta.llama3-1-70b-instruct-v1:0",
  "temperature": 0.7
}
```
**Use cases**:
- Open source preference
- Cost optimization
- Custom fine-tuning needs

## 🧪 **Quick Test Examples**

### **Test Bedrock Integration**
```bash
# Run the automated test script
./test_bedrock_integration.sh

# Or test API manually
curl -X GET "http://localhost:8000/api/v1/plugins/nodes/immutable_chat" | jq
```

### **Test Claude 3.5 Haiku (Fastest)**
```bash
# Create a Haiku test graph
curl -X POST "http://localhost:8000/api/v1/graphs/" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Bedrock Haiku Test",
    "nodes": {
      "input_1": {"node_id": "input_1", "node_type": "input", "parameters": {"label": "message"}},
      "haiku_chat": {"node_id": "haiku_chat", "node_type": "immutable_chat", "parameters": {"provider": "bedrock", "model": "us.anthropic.claude-3-5-haiku-20241022-v1:0", "temperature": 0.7}}
    },
    "edges": [{"edge_id": "e1", "source_node": "input_1", "source_port": "output", "target_node": "haiku_chat", "target_port": "message"}]
  }'

# Execute the test
curl -X POST "http://localhost:8000/api/v1/executions/" \
  -H "Content-Type: application/json" \
  -d '{
    "graph_id": "Bedrock Haiku Test",
    "inputs": {"message": "What is machine learning?"}
  }'
```

## 🔑 **Authentication Setup**

### **Option 1: Bearer Token (Recommended - Simple)**
1. Get your API key from [AWS Console > Bedrock > API Keys](https://console.aws.amazon.com/bedrock/)
2. Add to `.env`:
   ```bash
   AWS_BEARER_TOKEN_BEDROCK=your-bedrock-api-key-here
   AWS_REGION=us-east-1
   ```

### **Option 2: Full AWS Credentials**
1. Configure AWS credentials (CLI or environment)
2. Add to `.env`:
   ```bash
   AWS_ACCESS_KEY_ID=your-access-key
   AWS_SECRET_ACCESS_KEY=your-secret-key
   AWS_REGION=us-east-1
   ```

### **Restart After Configuration**
```bash
# Environment variables require restart
docker-compose down
docker-compose up -d
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
          "model": "us.anthropic.claude-3-5-sonnet-20241022-v1:0"
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
4. **Consider Llama** for open source requirements

### **Cost Optimization**
- Use **Haiku** for simple preprocessing steps
- Use **Sonnet** for main reasoning tasks
- Use **Opus** sparingly for critical decisions
- Chain models: Haiku → Sonnet → Output

### **Performance Tuning**
- **Lower temperature (0.1-0.3)** for factual tasks
- **Higher temperature (0.7-1.0)** for creative tasks
- **Adjust max_tokens** based on expected response length

## 🔍 **Troubleshooting**

### **Model Not Found Error**
```json
{"error": "Model not found: us.anthropic.claude-3-5-sonnet-20241022-v1:0"}
```
**Solutions**:
1. Verify the exact model ID spelling
2. Check if your AWS account has access to the model
3. Ensure the model is available in your AWS region
4. Try a legacy model like `anthropic.claude-3-haiku-20240307-v1:0`

### **Authentication Errors**
```json
{"error": "Authentication failed"}
```
**Solutions**:
1. Verify bearer token: `docker-compose exec backend env | grep AWS_BEARER_TOKEN_BEDROCK`
2. Check AWS region: `docker-compose exec backend env | grep AWS_REGION`
3. Restart containers: `docker-compose down && docker-compose up -d`

### **Rate Limits**
- **Haiku**: Higher rate limits
- **Sonnet**: Moderate rate limits  
- **Opus**: Lower rate limits
- **Llama**: Varies by model size

Use Haiku for high-volume testing and Sonnet/Opus for production.

## 📊 **Model Comparison**

| Model | Speed | Cost | Quality | Context Window | Best For |
|-------|-------|------|---------|----------------|----------|
| **Claude 3.5 Haiku** | ⚡ Very Fast | 💰 Cheapest | ⭐⭐⭐ Good | 200k tokens | Simple tasks, high volume |
| **Claude 3.5 Sonnet** | ⚡ Fast | 💰💰 Moderate | ⭐⭐⭐⭐ Excellent | 200k tokens | Most use cases |
| **Claude 3 Opus** | ⚡ Slower | 💰💰💰 Expensive | ⭐⭐⭐⭐⭐ Best | 200k tokens | Complex reasoning |
| **Llama 3.1 405B** | ⚡ Slow | 💰💰💰 Expensive | ⭐⭐⭐⭐ Very Good | 128k tokens | Open source, research |
| **Llama 3.1 70B** | ⚡ Moderate | 💰💰 Moderate | ⭐⭐⭐ Good | 128k tokens | Balanced open source |
| **Llama 3.1 8B** | ⚡ Very Fast | 💰 Cheapest | ⭐⭐ Fair | 128k tokens | Fast open source |

## 🌍 **Regional Availability**

**Primary Regions** (All models available):
- `us-east-1` (N. Virginia) - **Recommended**
- `us-west-2` (Oregon)
- `eu-west-1` (Ireland)

**Secondary Regions** (Limited models):
- `ap-southeast-1` (Singapore)
- `ap-northeast-1` (Tokyo)

**Set in .env**:
```bash
AWS_REGION=us-east-1  # Recommended for full model access
```

Choose based on your specific needs for speed, cost, quality, and regional requirements!