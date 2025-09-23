#!/bin/bash

# Plugin Auto-Discovery System Test Script
# Run this to verify that custom plugins are properly discovered and functional.

set -e  # Exit on any error

BASE_URL="http://localhost:8000"
TIMEOUT=30
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}🧪 Plugin Auto-Discovery System Test${NC}"
echo "========================================"

# Function to wait for backend
wait_for_backend() {
    echo "Waiting for backend to be ready..."
    for i in $(seq 1 $TIMEOUT); do
        if curl -s "$BASE_URL/api/v1/plugins/nodes" > /dev/null 2>&1; then
            echo -e "${GREEN}✅ Backend is ready!${NC}"
            return 0
        fi
        echo "   Still waiting... ($i/$TIMEOUT)"
        sleep 1
    done
    echo -e "${RED}❌ Backend not ready after $TIMEOUT seconds${NC}"
    return 1
}

# Function to test plugin discovery
test_plugin_discovery() {
    echo -e "\n${BLUE}📦 Testing plugin auto-discovery...${NC}"
    
    # Get list of available nodes
    nodes_response=$(curl -s "$BASE_URL/api/v1/plugins/nodes")
    if [ $? -ne 0 ]; then
        echo -e "${RED}❌ Failed to fetch available nodes${NC}"
        return 1
    fi
    
    # Check for auto-discovered nodes
    expected_nodes=("example_processor" "simple_text_processor")
    found_count=0
    
    for node_type in "${expected_nodes[@]}"; do
        if echo "$nodes_response" | jq -e ".[] | select(.node_type == \"$node_type\")" > /dev/null 2>&1; then
            echo -e "${GREEN}✅ Found auto-discovered node: $node_type${NC}"
            ((found_count++))
        else
            echo -e "${RED}❌ Missing auto-discovered node: $node_type${NC}"
        fi
    done
    
    if [ $found_count -eq ${#expected_nodes[@]} ]; then
        echo -e "${GREEN}✅ All ${#expected_nodes[@]} auto-discovered nodes found!${NC}"
        return 0
    else
        echo -e "${RED}❌ Only found $found_count/${#expected_nodes[@]} expected nodes${NC}"
        return 1
    fi
}

# Function to create test graph
create_test_graph() {
    echo -e "\n${BLUE}🔧 Creating test graph with auto-discovered nodes...${NC}"
    
    graph_response=$(curl -s -X POST "$BASE_URL/api/v1/graphs/" \
        -H "Content-Type: application/json" \
        -d '{
            "name": "Plugin Auto-Discovery Test",
            "description": "Test graph for auto-discovered plugin functionality",
            "nodes": {
                "input_text": {
                    "node_id": "input_text",
                    "node_type": "input",
                    "position": {"x": 50, "y": 100},
                    "parameters": {
                        "label": "test_input",
                        "data_type": "text",
                        "value": "Hello from auto-discovered plugin test"
                    }
                },
                "word_counter": {
                    "node_id": "word_counter",
                    "node_type": "example_processor",
                    "position": {"x": 250, "y": 100},
                    "parameters": {
                        "processing_type": "word_count"
                    }
                },
                "text_enhancer": {
                    "node_id": "text_enhancer", 
                    "node_type": "simple_text_processor",
                    "position": {"x": 450, "y": 100},
                    "parameters": {
                        "operation": "exclamation"
                    }
                },
                "final_result": {
                    "node_id": "final_result",
                    "node_type": "output",
                    "position": {"x": 650, "y": 100},
                    "parameters": {
                        "label": "Test Result"
                    }
                }
            },
            "edges": [
                {
                    "edge_id": "input_to_counter",
                    "source_node": "input_text",
                    "source_port": "output",
                    "target_node": "word_counter",
                    "target_port": "text"
                },
                {
                    "edge_id": "counter_to_enhancer",
                    "source_node": "word_counter",
                    "source_port": "count_string", 
                    "target_node": "text_enhancer",
                    "target_port": "text"
                },
                {
                    "edge_id": "enhancer_to_output",
                    "source_node": "text_enhancer",
                    "source_port": "result",
                    "target_node": "final_result",
                    "target_port": "input"
                }
            ]
        }')
    
    if [ $? -ne 0 ]; then
        echo -e "${RED}❌ Failed to create test graph${NC}"
        return 1
    fi
    
    graph_id=$(echo "$graph_response" | jq -r '.id // empty')
    if [ -z "$graph_id" ] || [ "$graph_id" = "null" ]; then
        echo -e "${RED}❌ Failed to get graph ID from response${NC}"
        echo "Response: $graph_response" | head -5
        return 1
    fi
    
    echo -e "${GREEN}✅ Created test graph with ID: $graph_id${NC}"
    echo "$graph_id"
    return 0
}

# Function to test graph execution
test_graph_execution() {
    local graph_id="$1"
    echo -e "\n${BLUE}🚀 Testing graph execution...${NC}"
    
    execution_response=$(curl -s -X POST "$BASE_URL/api/v1/executions/" \
        -H "Content-Type: application/json" \
        -d "{\"graph_id\": \"$graph_id\"}")
    
    if [ $? -ne 0 ]; then
        echo -e "${RED}❌ Failed to execute graph${NC}"
        return 1
    fi
    
    status=$(echo "$execution_response" | jq -r '.status // "unknown"')
    
    if [ "$status" = "completed" ]; then
        echo -e "${GREEN}✅ Graph executed successfully!${NC}"
        
        # Extract and display results - try multiple possible output paths
        result=$(echo "$execution_response" | jq -r '.outputs.final_result.result // .outputs.test_output.result // .outputs | to_entries | map(select(.value.label != null)) | first.value.result // "Success"')
        echo "   Result: $result"
        
        return 0
    elif [ "$status" = "failed" ]; then
        echo -e "${RED}❌ Graph execution failed with status: $status${NC}"
        
        # Show errors if available
        errors=$(echo "$execution_response" | jq -r '.errors | to_entries | map("\(.key): \(.value)") | join(", ") // "No error details"')
        echo "   Errors: $errors"
        return 1
    else
        echo -e "${RED}❌ Graph execution returned unexpected status: $status${NC}"
        echo "   Full response: $(echo "$execution_response" | jq -c '.')"
        return 1
    fi
}

# Function to cleanup test graph
cleanup_test_graph() {
    local graph_id="$1"
    echo -e "\n${BLUE}🧹 Cleaning up test graph...${NC}"
    
    curl -s -X DELETE "$BASE_URL/api/v1/graphs/$graph_id" > /dev/null 2>&1
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✅ Test graph cleaned up${NC}"
    else
        echo -e "${YELLOW}⚠️  Could not clean up test graph${NC}"
    fi
}

# Main execution
main() {
    # Check dependencies
    if ! command -v curl &> /dev/null; then
        echo -e "${RED}❌ curl is required but not installed${NC}"
        exit 1
    fi
    
    if ! command -v jq &> /dev/null; then
        echo -e "${RED}❌ jq is required but not installed${NC}"
        exit 1
    fi
    
    # Wait for backend
    if ! wait_for_backend; then
        exit 1
    fi
    
    # Test plugin discovery
    if ! test_plugin_discovery; then
        exit 1
    fi
    
    # Create test graph
    graph_id=$(create_test_graph)
    if [ $? -ne 0 ]; then
        exit 1
    fi
    
    # Test graph execution
    if test_graph_execution "$graph_id"; then
        success=true
    else
        success=false
    fi
    
    # Clean up
    cleanup_test_graph "$graph_id"
    
    if [ "$success" = true ]; then
        echo -e "\n${GREEN}🎉 All tests passed! Plugin auto-discovery system is working correctly.${NC}"
        exit 0
    else
        echo -e "\n${RED}❌ Some tests failed. Check the output above for details.${NC}"
        exit 1
    fi
}

main "$@"