#!/usr/bin/env python3
"""
Test script for plugin auto-discovery system.
Run this to verify that custom plugins are properly discovered and functional.
"""

import json
import requests
import time
import sys

BASE_URL = "http://localhost:8000"

def wait_for_backend():
    """Wait for backend to be ready."""
    print("Waiting for backend to be ready...")
    for i in range(30):  # Wait up to 30 seconds
        try:
            response = requests.get(f"{BASE_URL}/api/v1/plugins/nodes", timeout=2)
            if response.status_code == 200:
                print("✅ Backend is ready!")
                return True
        except requests.exceptions.RequestException:
            pass
        time.sleep(1)
        print(f"   Still waiting... ({i+1}/30)")
    
    print("❌ Backend not ready after 30 seconds")
    return False

def test_plugin_discovery():
    """Test that auto-discovered plugins are loaded."""
    print("\n📦 Testing plugin auto-discovery...")
    
    try:
        response = requests.get(f"{BASE_URL}/api/v1/plugins/nodes")
        response.raise_for_status()
        
        nodes = response.json()
        node_types = [node['node_type'] for node in nodes]
        
        # Check for our auto-discovered nodes
        expected_nodes = ['example_processor', 'simple_text_processor']
        found_nodes = []
        
        for node_type in expected_nodes:
            if node_type in node_types:
                found_nodes.append(node_type)
                print(f"✅ Found auto-discovered node: {node_type}")
            else:
                print(f"❌ Missing auto-discovered node: {node_type}")
        
        if len(found_nodes) == len(expected_nodes):
            print(f"✅ All {len(expected_nodes)} auto-discovered nodes found!")
            return True
        else:
            print(f"❌ Only found {len(found_nodes)}/{len(expected_nodes)} expected nodes")
            return False
            
    except Exception as e:
        print(f"❌ Error testing plugin discovery: {e}")
        return False

def create_test_graph():
    """Create a test graph using auto-discovered nodes."""
    print("\n🔧 Creating test graph with auto-discovered nodes...")
    
    graph_data = {
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
    }
    
    try:
        response = requests.post(f"{BASE_URL}/api/v1/graphs/", json=graph_data)
        response.raise_for_status()
        
        graph = response.json()
        graph_id = graph['id']
        print(f"✅ Created test graph with ID: {graph_id}")
        return graph_id
        
    except Exception as e:
        print(f"❌ Error creating test graph: {e}")
        return None

def test_graph_execution(graph_id):
    """Test execution of the graph with auto-discovered nodes."""
    print(f"\n🚀 Testing graph execution...")
    
    try:
        # Execute by graph ID
        execution_data = {"graph_id": graph_id}
        response = requests.post(f"{BASE_URL}/api/v1/executions/", json=execution_data)
        response.raise_for_status()
        
        execution = response.json()
        
        if execution['status'] == 'completed':
            print("✅ Graph executed successfully!")
            
            # Print the results
            if 'outputs' in execution and execution['outputs']:
                for node_id, output in execution['outputs'].items():
                    if isinstance(output, dict) and 'label' in output:
                        print(f"   {output['label']}: {output.get('result', 'N/A')}")
            
            return True
        else:
            print(f"❌ Graph execution failed with status: {execution['status']}")
            if 'errors' in execution:
                for error_type, error_msg in execution['errors'].items():
                    print(f"   {error_type}: {error_msg}")
            return False
            
    except Exception as e:
        print(f"❌ Error executing graph: {e}")
        return False

def cleanup_test_graph(graph_id):
    """Clean up the test graph."""
    print(f"\n🧹 Cleaning up test graph...")
    try:
        response = requests.delete(f"{BASE_URL}/api/v1/graphs/{graph_id}")
        if response.status_code in [200, 204]:
            print("✅ Test graph cleaned up")
        else:
            print(f"⚠️  Could not clean up test graph (status {response.status_code})")
    except Exception as e:
        print(f"⚠️  Error cleaning up test graph: {e}")

def main():
    """Main test function."""
    print("🧪 Plugin Auto-Discovery System Test")
    print("=" * 40)
    
    # Wait for backend
    if not wait_for_backend():
        sys.exit(1)
    
    # Test plugin discovery
    if not test_plugin_discovery():
        sys.exit(1)
    
    # Create and test graph
    graph_id = create_test_graph()
    if not graph_id:
        sys.exit(1)
    
    # Test graph execution
    success = test_graph_execution(graph_id)
    
    # Clean up
    cleanup_test_graph(graph_id)
    
    if success:
        print("\n🎉 All tests passed! Plugin auto-discovery system is working correctly.")
        sys.exit(0)
    else:
        print("\n❌ Some tests failed. Check the output above for details.")
        sys.exit(1)

if __name__ == "__main__":
    main()