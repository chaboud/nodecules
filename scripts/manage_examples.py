#!/usr/bin/env python3
"""
Nodecules Example Graph Management Script

Handles setup and cleanup of example graphs for testing and demonstration.
"""

import asyncio
import json
import sys
import os
from pathlib import Path

# Add the backend to the path so we can import nodecules
sys.path.append(str(Path(__file__).parent.parent / "backend"))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from nodecules.models.graph import Graph
from nodecules.core.database import get_database_url

# Example graphs to install
EXAMPLE_GRAPHS = {
    "simple_chat_test": {
        "name": "simple_chat_test",
        "description": "Simple chat graph following chat_message/chat_response convention",
        "nodes": {
            "chat_message": {
                "node_id": "chat_message",
                "node_type": "input",
                "position": {"x": 100, "y": 200},
                "parameters": {"label": "message", "value": "", "data_type": "text"},
                "description": "User message input"
            },
            "chat_context": {
                "node_id": "chat_context", 
                "node_type": "input",
                "position": {"x": 100, "y": 300},
                "parameters": {"label": "context_key", "value": "", "data_type": "text"},
                "description": "Previous conversation context"
            },
            "temperature_control": {
                "node_id": "temperature_control",
                "node_type": "input", 
                "position": {"x": 100, "y": 400},
                "parameters": {"label": "temperature", "value": "0.7", "data_type": "number"},
                "description": "Response temperature control"
            },
            "chat_ai": {
                "node_id": "chat_ai",
                "node_type": "immutable_chat",
                "position": {"x": 400, "y": 250},
                "parameters": {
                    "provider": "ollama",
                    "model": "llama3.2:3b", 
                    "system_prompt": "You are a helpful AI assistant.",
                    "temperature": 0.7
                },
                "description": "AI chat processing"
            },
            "chat_response": {
                "node_id": "chat_response",
                "node_type": "output",
                "position": {"x": 700, "y": 200},
                "parameters": {"label": "result"},
                "description": "AI response output"
            },
            "new_context": {
                "node_id": "new_context", 
                "node_type": "output",
                "position": {"x": 700, "y": 350},
                "parameters": {"label": "context_key"},
                "description": "New context for next turn"
            }
        },
        "edges": [
            {"edge_id": "msg_to_ai", "source_node": "chat_message", "target_node": "chat_ai", "source_port": "output", "target_port": "message"},
            {"edge_id": "ctx_to_ai", "source_node": "chat_context", "target_node": "chat_ai", "source_port": "output", "target_port": "context_key"},
            {"edge_id": "temp_to_ai", "source_node": "temperature_control", "target_node": "chat_ai", "source_port": "output", "target_port": "temperature"},
            {"edge_id": "ai_to_response", "source_node": "chat_ai", "target_node": "chat_response", "source_port": "response", "target_port": "input"},
            {"edge_id": "ai_to_context", "source_node": "chat_ai", "target_node": "new_context", "source_port": "context_key", "target_port": "input"}
        ],
        "metadata": {"example": True, "type": "chat"}
    },
    
    "text_processing_demo": {
        "name": "text_processing_demo",
        "description": "Demonstrates text processing capabilities",
        "nodes": {
            "user_input": {
                "node_id": "user_input",
                "node_type": "input",
                "position": {"x": 100, "y": 150},
                "parameters": {"label": "text", "value": "Hello World", "data_type": "text"},
                "description": "Text to process"
            },
            "transform": {
                "node_id": "transform",
                "node_type": "text_transform",
                "position": {"x": 300, "y": 150},
                "parameters": {"operation": "uppercase"},
                "description": "Transform text to uppercase"
            },
            "result": {
                "node_id": "result",
                "node_type": "output",
                "position": {"x": 500, "y": 150},
                "parameters": {"label": "result"},
                "description": "Processed text output"
            }
        },
        "edges": [
            {"edge_id": "input_to_transform", "source_node": "user_input", "target_node": "transform", "source_port": "output", "target_port": "text"},
            {"edge_id": "transform_to_output", "source_node": "transform", "target_node": "result", "source_port": "output", "target_port": "input"}
        ],
        "metadata": {"example": True, "type": "processing"}
    }
}

def get_db_session():
    """Get database session"""
    engine = create_engine(get_database_url())
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return SessionLocal()

def install_examples():
    """Install example graphs"""
    db = get_db_session()
    try:
        installed = 0
        for graph_key, graph_data in EXAMPLE_GRAPHS.items():
            # Check if already exists
            existing = db.query(Graph).filter(Graph.name == graph_data["name"]).first()
            if existing:
                print(f"⚠️  Graph '{graph_data['name']}' already exists, skipping")
                continue
                
            # Create new graph
            graph = Graph(
                name=graph_data["name"],
                description=graph_data["description"],
                nodes=graph_data["nodes"],
                edges=graph_data["edges"],
                metadata=graph_data["metadata"],
                created_by="system"
            )
            db.add(graph)
            installed += 1
            print(f"✅ Installed example graph: {graph_data['name']}")
            
        db.commit()
        print(f"\n🎉 Successfully installed {installed} example graphs!")
        
    except Exception as e:
        db.rollback()
        print(f"❌ Error installing examples: {e}")
        raise
    finally:
        db.close()

def cleanup_examples():
    """Remove example graphs"""
    db = get_db_session()
    try:
        # Delete graphs marked as examples
        result = db.execute(
            text("DELETE FROM graphs WHERE metadata->>'example' = 'true'")
        )
        db.commit()
        
        print(f"🧹 Cleaned up {result.rowcount} example graphs")
        
    except Exception as e:
        db.rollback()
        print(f"❌ Error cleaning up examples: {e}")
        raise
    finally:
        db.close()

def list_examples():
    """List currently installed example graphs"""
    db = get_db_session()
    try:
        examples = db.execute(
            text("SELECT name, description FROM graphs WHERE metadata->>'example' = 'true'")
        ).fetchall()
        
        if examples:
            print("📋 Installed example graphs:")
            for name, desc in examples:
                print(f"  • {name}: {desc}")
        else:
            print("📭 No example graphs currently installed")
            
    except Exception as e:
        print(f"❌ Error listing examples: {e}")
        raise
    finally:
        db.close()

def main():
    """Main CLI interface"""
    if len(sys.argv) < 2:
        print("""
Nodecules Example Graph Manager

Usage:
  python manage_examples.py <command>

Commands:
  install    Install example graphs
  cleanup    Remove all example graphs  
  list       List installed example graphs
  reset      Cleanup then install (fresh start)
        """)
        return
        
    command = sys.argv[1].lower()
    
    if command == "install":
        install_examples()
    elif command == "cleanup":
        cleanup_examples()
    elif command == "list":
        list_examples()
    elif command == "reset":
        print("🔄 Resetting example graphs...")
        cleanup_examples()
        install_examples()
    else:
        print(f"❌ Unknown command: {command}")
        return 1

if __name__ == "__main__":
    main()