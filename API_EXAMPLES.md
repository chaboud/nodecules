# Nodecules API Examples

This document provides practical examples for using the Nodecules API, including the "Potato Farmer!" sample workflow with jq piping techniques.

## 🚀 Quick API Reference

### Base URL
- Development: `http://localhost:8000/api/v1`
- Production: Update accordingly

### Authentication
Currently no authentication required (development mode)

## 🥔 Potato Farmer! Graph Examples

The "Potato Farmer!" graph is a sample workflow that demonstrates text processing capabilities. It concatenates text inputs, transforms them to uppercase, and outputs a rallying cry for potato farmers.

### Graph Structure
- **3 Input nodes**: "Potato farmers of the world", "Unite", "!!!"  
- **2 Text Concat nodes**: Combine inputs together
- **1 Text Transform node**: Converts to uppercase
- **1 Output node**: Displays final result

### Basic Examples

#### 1. Execute with Default Values
```bash
# Basic execution - uses default input values
curl -s -X POST http://localhost:8000/api/v1/executions/ \
  -H "Content-Type: application/json" \
  -d '{"graph_id": "Potato farmer!"}' | \
  jq -r '.outputs | to_entries[] | select(.value.label == "Output") | .value.result'
```
**Output**: `POTATO FARMERS OF THE WORLD UNITE !!!`

#### 2. Execute with Custom Input (Ordinal)
```bash
# Change the first input using ordinal key
curl -s -X POST http://localhost:8000/api/v1/executions/ \
  -H "Content-Type: application/json" \
  -d '{
    "graph_id": "Potato farmer!",
    "inputs": {
      "input_1": "Elite potato farmers from Idaho"
    }
  }' | jq -r '.outputs | to_entries[] | select(.value.label == "Output") | .value.result'
```
**Output**: `ELITE POTATO FARMERS FROM IDAHO UNITE !!!`

#### 3. Execute with Multiple Custom Inputs
```bash
# Change multiple inputs using ordinals
curl -s -X POST http://localhost:8000/api/v1/executions/ \
  -H "Content-Type: application/json" \
  -d '{
    "graph_id": "Potato farmer!",
    "inputs": {
      "input_1": "Elite potato farmers from Idaho",
      "input_2": "FIGHT THE POWER",
      "input_3": "FOR BETTER SPUDS!!!"
    }
  }' | jq -r '.outputs | to_entries[] | select(.value.label == "Output") | .value.result'
```
**Output**: `ELITE POTATO FARMERS FROM IDAHO FIGHT THE POWER FOR BETTER SPUDS!!!`

## 🔍 jq Piping Techniques

### Extract Just the Final Result
```bash
# Get only the output result text (no quotes)
curl -s -X POST http://localhost:8000/api/v1/executions/ \
  -H "Content-Type: application/json" \
  -d '{"graph_id": "Potato farmer!"}' | \
  jq -r '.outputs | to_entries[] | select(.value.label == "Output") | .value.result'
```

### View All Outputs with Labels
```bash
# See all node outputs with their labels
curl -s -X POST http://localhost:8000/api/v1/executions/ \
  -H "Content-Type: application/json" \
  -d '{"graph_id": "Potato farmer!"}' | \
  jq '.outputs | to_entries[] | {node: .key, result: .value.result, label: .value.label}'
```

### Extract Execution Status
```bash
# Get execution status and timing
curl -s -X POST http://localhost:8000/api/v1/executions/ \
  -H "Content-Type: application/json" \
  -d '{"graph_id": "Potato farmer!"}' | \
  jq '{status, started_at, completed_at, duration: (.completed_at | strptime("%Y-%m-%dT%H:%M:%S.%f") | mktime) - (.started_at | strptime("%Y-%m-%dT%H:%M:%S.%f") | mktime)}'
```

### Check for Errors
```bash
# Show any execution errors
curl -s -X POST http://localhost:8000/api/v1/executions/ \
  -H "Content-Type: application/json" \
  -d '{"graph_id": "Potato farmer!"}' | \
  jq 'if .errors | length > 0 then .errors else "No errors" end'
```

## 📊 Graph Schema and Introspection

### Get Graph Schema
```bash
# View graph input/output schema with friendly names
curl -s "http://localhost:8000/api/v1/graphs/Potato%20farmer!/schema" | jq '.'
```

### Extract Input Information
```bash
# Get just the input specifications
curl -s "http://localhost:8000/api/v1/graphs/Potato%20farmer!/schema" | \
  jq '.inputs[] | {ordinal_key, label, data_type, default_value, description}'
```

### Get Example API Call
```bash
# Extract the example API call from schema
curl -s "http://localhost:8000/api/v1/graphs/Potato%20farmer!/schema" | \
  jq '.example_call'
```

## 🔧 Graph Management Examples

### List All Graphs
```bash
# Get all graph names
curl -s "http://localhost:8000/api/v1/graphs/" | jq '.[].name'

# Get graph names with IDs
curl -s "http://localhost:8000/api/v1/graphs/" | \
  jq '.[] | {name, id, created_at}'
```

### Get Graph by Name (Case-Insensitive)
```bash
# These all work for "Potato farmer!"
curl -s "http://localhost:8000/api/v1/graphs/potato%20farmer!" | jq '.name'
curl -s "http://localhost:8000/api/v1/graphs/POTATO%20FARMER!" | jq '.name'
curl -s "http://localhost:8000/api/v1/graphs/Potato%20farmer!" | jq '.name'
```

### Copy a Graph
```bash
# Create a copy of the Potato farmer graph
curl -s -X POST "http://localhost:8000/api/v1/graphs/Potato%20farmer!/copy" | \
  jq '{name, id, created_at}'
```

## 🎯 Advanced Usage Patterns

### Piping Multiple Commands
```bash
# Get graph schema, then execute with custom input
GRAPH_NAME="Potato farmer!"
echo "Graph inputs:"
curl -s "http://localhost:8000/api/v1/graphs/${GRAPH_NAME// /%20}/schema" | \
  jq -r '.inputs[] | "- \(.ordinal_key): \(.description) (default: \(.default_value))"'

echo -e "\nExecuting with custom input:"
curl -s -X POST http://localhost:8000/api/v1/executions/ \
  -H "Content-Type: application/json" \
  -d "{\"graph_id\": \"$GRAPH_NAME\", \"inputs\": {\"input_2\": \"REVOLUTION\"}}" | \
  jq -r '.outputs | to_entries[] | select(.value.label == "Output") | .value.result'
```

### Execution with Error Handling
```bash
# Execute with error checking
RESULT=$(curl -s -X POST http://localhost:8000/api/v1/executions/ \
  -H "Content-Type: application/json" \
  -d '{"graph_id": "Potato farmer!"}')

if echo "$RESULT" | jq -e '.errors | length > 0' > /dev/null; then
  echo "Execution failed:"
  echo "$RESULT" | jq '.errors'
else
  echo "Success:"
  echo "$RESULT" | jq -r '.outputs | to_entries[] | select(.value.label == "Output") | .value.result'
fi
```

### Batch Processing
```bash
# Execute the same graph with different inputs
for input in "Farmers" "Workers" "Coders"; do
  echo "Processing: $input"
  curl -s -X POST http://localhost:8000/api/v1/executions/ \
    -H "Content-Type: application/json" \
    -d "{\"graph_id\": \"Potato farmer!\", \"inputs\": {\"input_1\": \"$input\"}}" | \
    jq -r '.outputs | to_entries[] | select(.value.label == "Output") | .value.result'
  echo
done
```

## 🧪 Testing and Validation

### Validate Graph Exists
```bash
# Check if a graph exists
if curl -s "http://localhost:8000/api/v1/graphs/Potato%20farmer!" | jq -e '.name' > /dev/null; then
  echo "Graph exists"
else
  echo "Graph not found"
fi
```

### Performance Testing
```bash
# Time execution
time curl -s -X POST http://localhost:8000/api/v1/executions/ \
  -H "Content-Type: application/json" \
  -d '{"graph_id": "Potato farmer!"}' | \
  jq -r '.outputs | to_entries[] | select(.value.label == "Output") | .value.result'
```

## 📝 Curl Options Reference

### Essential Curl Flags
- `-s` - Silent mode (no progress/stats)
- `-S` - Show errors even in silent mode
- `-f` - Fail silently on HTTP errors
- `-X POST` - Specify HTTP method
- `-H "Content-Type: application/json"` - Set JSON content type
- `-d @file.json` - Send data from file
- `-d '{...}'` - Send inline JSON data

### Robust Production Usage
```bash
# Production-ready curl command with error handling
curl -sSf -X POST http://localhost:8000/api/v1/executions/ \
  -H "Content-Type: application/json" \
  -d '{"graph_id": "Potato farmer!"}' | \
  jq -r '.outputs | to_entries[] | select(.value.label == "Output") | .value.result'
```

## 🎉 Quick Integration Examples

### Shell Script Integration
```bash
#!/bin/bash
# execute_graph.sh
GRAPH_NAME="$1"
INPUT_TEXT="$2"

if [ -z "$GRAPH_NAME" ] || [ -z "$INPUT_TEXT" ]; then
  echo "Usage: $0 <graph_name> <input_text>"
  exit 1
fi

curl -sSf -X POST http://localhost:8000/api/v1/executions/ \
  -H "Content-Type: application/json" \
  -d "{\"graph_id\": \"$GRAPH_NAME\", \"inputs\": {\"input_1\": \"$INPUT_TEXT\"}}" | \
  jq -r '.outputs | to_entries[] | select(.value.label == "Output") | .value.result'
```

### Python Integration
```python
import requests
import json

def execute_graph(graph_name, inputs=None):
    url = "http://localhost:8000/api/v1/executions/"
    payload = {"graph_id": graph_name, "inputs": inputs or {}}
    
    response = requests.post(url, json=payload)
    response.raise_for_status()
    
    result = response.json()
    
    # Extract output result
    for node_id, output in result["outputs"].items():
        if output.get("label") == "Output":
            return output["result"]
    
    return None

# Usage
result = execute_graph("Potato farmer!", {"input_1": "Elite farmers"})
print(result)  # ELITE FARMERS UNITE !!!
```

---

**Note**: All examples assume the development server is running at `localhost:8000`. Adjust URLs for your environment.

**Pro Tip**: Use `curl -sSf` for production scripts to handle errors gracefully.