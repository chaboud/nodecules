#!/usr/bin/env python3
"""Test script to verify complete workflow with built-in nodes."""

import requests
import json
import time

BASE_URL = "http://localhost:8000/api/v1"

def test_workflow():
    print("🧪 Testing complete workflow with built-in nodes...")
    
    # 1. Check available nodes
    print("\n1. Checking available node types...")
    response = requests.get(f"{BASE_URL}/plugins/nodes")
    nodes = response.json()
    print(f"Available nodes: {[n['node_type'] for n in nodes]}")
    
    # 2. Create a test graph: Input -> Text Transform -> Output
    print("\n2. Creating test graph...")
    graph_data = {
        "name": "Built-in Nodes Test",
        "description": "Test graph using input, text_transform, and output nodes",
        "nodes": {
            "input_1": {
                "node_id": "input_1",
                "node_type": "input",
                "position": {"x": 100, "y": 100},
                "parameters": {
                    "value": "hello world",
                    "data_type": "text"
                }
            },
            "transform_1": {
                "node_id": "transform_1", 
                "node_type": "text_transform",
                "position": {"x": 300, "y": 100},
                "parameters": {
                    "operation": "uppercase"
                }
            },
            "output_1": {
                "node_id": "output_1",
                "node_type": "output", 
                "position": {"x": 500, "y": 100},
                "parameters": {
                    "label": "Result"
                }
            }
        },
        "edges": [
            {
                "edge_id": "edge_1",
                "source_node": "input_1",
                "source_port": "output",
                "target_node": "transform_1", 
                "target_port": "text"
            },
            {
                "edge_id": "edge_2",
                "source_node": "transform_1",
                "source_port": "output", 
                "target_node": "output_1",
                "target_port": "input"
            }
        ],
        "metadata": {}
    }
    
    response = requests.post(f"{BASE_URL}/graphs/", json=graph_data)
    if response.status_code != 200:
        print(f"❌ Failed to create graph: {response.text}")
        return False
        
    graph = response.json()
    graph_id = graph["id"]
    print(f"✅ Created graph with ID: {graph_id}")
    
    # 3. Execute the graph with user inputs
    print("\n3. Executing graph with user input...")
    execution_data = {
        "graph_id": graph_id,
        "inputs": {
            "input_1": "test message from user!"  # Override the default input
        }
    }
    
    response = requests.post(f"{BASE_URL}/executions/", json=execution_data)
    if response.status_code != 200:
        print(f"❌ Failed to execute graph: {response.text}")
        return False
        
    execution = response.json()
    execution_id = execution["id"]
    print(f"✅ Started execution with ID: {execution_id}")
    print(f"Execution status: {execution['status']}")
    
    # 4. Check execution results
    print("\n4. Checking execution results...")
    response = requests.get(f"{BASE_URL}/executions/{execution_id}")
    execution = response.json()
    
    print(f"Final status: {execution['status']}")
    if execution.get('node_status'):
        print("Node statuses:")
        for node_id, status in execution['node_status'].items():
            print(f"  {node_id}: {status}")
    
    if execution.get('outputs'):
        print("Node outputs:")
        for node_id, outputs in execution['outputs'].items():
            print(f"  {node_id}: {outputs}")
    
    if execution.get('errors'):
        print("Errors:")
        for node_id, error in execution['errors'].items():
            print(f"  {node_id}: {error}")
    
    print(f"\n✅ Workflow test completed!")
    return execution['status'] == 'completed'

if __name__ == "__main__":
    success = test_workflow()
    exit(0 if success else 1)