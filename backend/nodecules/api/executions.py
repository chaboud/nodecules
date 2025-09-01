"""API routes for graph execution."""

import logging
from datetime import datetime
from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
import json

from ..models.database import get_database
from ..models.schemas import Graph, Execution
from ..core.types import GraphData, NodeData, EdgeData
from ..core.executor import GraphExecutor
from .models import ExecutionCreateRequest, ExecutionResponse, ErrorResponse, ContextAction
from .graphs import resolve_graph_by_id_or_name

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/", response_model=ExecutionResponse)
async def execute_graph(
    request: ExecutionCreateRequest,
    fastapi_request: Request,
    db: Session = Depends(get_database)
):
    """Execute a graph with optional context management."""
    try:
        # Handle context actions first
        execution_inputs = request.inputs.copy()
        
        if request.context_action:
            context_action = request.context_action
            
            if context_action.action == "continue" and context_action.context_id:
                # Continue from existing context - add context to inputs
                execution_inputs["_context"] = context_action.context_id
                
            elif context_action.action == "rewind" and context_action.context_id:
                # Rewind context first, then continue
                from ..core.context_service import context_service
                
                # Load and rewind context
                current_context = await context_service.get_context(context_action.context_id, db)
                if current_context:
                    # Simple rewind by removing last N message pairs
                    steps_back = context_action.rewind_steps or 1
                    messages_to_remove = steps_back * 2  # user + assistant pairs
                    
                    if messages_to_remove < len(current_context.messages):
                        current_context.messages = current_context.messages[:-messages_to_remove]
                    
                    # Create new context step and store
                    rewound_context = current_context.create_next_step()
                    await context_service.store_context(rewound_context, db)
                    
                    execution_inputs["_context"] = rewound_context.context_id
                
            elif context_action.action == "new":
                # Create new context if conversation_id specified
                if context_action.conversation_id:
                    execution_inputs["_conversation_id"] = context_action.conversation_id
        
        # Get graph from database by ID or name
        db_graph = resolve_graph_by_id_or_name(request.graph_id, db)
        
        # Convert to internal format
        nodes = {}
        for node_id, node_data in db_graph.nodes.items():
            nodes[node_id] = NodeData(
                node_id=node_data["node_id"],
                node_type=node_data["node_type"],
                position=node_data.get("position", {}),
                parameters=node_data.get("parameters", {}),
                label=node_data.get("label"),
                description=node_data.get("description")
            )
            
        edges = []
        for edge_data in db_graph.edges:
            edges.append(EdgeData(
                edge_id=edge_data["edge_id"],
                source_node=edge_data["source_node"],
                source_port=edge_data["source_port"],
                target_node=edge_data["target_node"],
                target_port=edge_data["target_port"]
            ))
        
        graph_data = GraphData(
            graph_id=str(db_graph.id),
            name=db_graph.name,
            nodes=nodes,
            edges=edges,
            meta_data=db_graph.meta_data
        )
        
        # Create execution record
        db_execution = Execution(
            graph_id=db_graph.id,
            status="pending",
            inputs=execution_inputs  # Use processed inputs with context
        )
        db.add(db_execution)
        db.commit()
        db.refresh(db_execution)
        
        try:
            # Get node registry from app state
            node_registry = fastapi_request.app.state.node_registry
            
            # Execute graph
            executor = GraphExecutor(node_registry.get_all())
            context = await executor.execute_graph(graph_data, execution_inputs)
            
            # Update execution record with results
            db_execution.status = "completed"
            db_execution.outputs = context.node_outputs
            db_execution.node_status = {k: v.value for k, v in context.node_status.items()}
            db_execution.errors = context.errors
            db_execution.started_at = context.started_at
            db_execution.completed_at = context.completed_at
            
        except Exception as e:
            # Update execution record with error
            db_execution.status = "failed"
            db_execution.errors = {"execution": str(e)}
            logger.error(f"Graph execution failed: {e}")
            
        db.commit()
        db.refresh(db_execution)
        
        logger.info(f"Execution {db_execution.id} status: {db_execution.status}")
        return ExecutionResponse.model_validate(db_execution)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to execute graph: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{execution_id}", response_model=ExecutionResponse)
async def get_execution(
    execution_id: UUID,
    db: Session = Depends(get_database)
):
    """Get execution status and results."""
    execution = db.query(Execution).filter(Execution.id == execution_id).first()
    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")
    
    return ExecutionResponse.model_validate(execution)


@router.get("/", response_model=List[ExecutionResponse])
async def list_executions(
    graph_id: UUID = None,
    status: str = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_database)
):
    """List executions with optional filtering."""
    query = db.query(Execution)
    
    if graph_id:
        query = query.filter(Execution.graph_id == graph_id)
    if status:
        query = query.filter(Execution.status == status)
    
    executions = query.offset(skip).limit(limit).all()
    return [ExecutionResponse.model_validate(execution) for execution in executions]


@router.post("/stream", response_class=StreamingResponse)
async def execute_graph_streaming(
    request: ExecutionCreateRequest,
    fastapi_request: Request,
    db: Session = Depends(get_database)
):
    """Execute a graph with streaming responses."""
    try:
        # Handle context actions first (same as regular execution)
        execution_inputs = request.inputs.copy()
        
        if request.context_action:
            context_action = request.context_action
            
            if context_action.action == "continue" and context_action.context_id:
                execution_inputs["_context"] = context_action.context_id
                
            elif context_action.action == "rewind" and context_action.context_id:
                from ..core.context_service import context_service
                
                current_context = await context_service.get_context(context_action.context_id, db)
                if current_context:
                    steps_back = context_action.rewind_steps or 1
                    messages_to_remove = steps_back * 2
                    
                    if messages_to_remove < len(current_context.messages):
                        current_context.messages = current_context.messages[:-messages_to_remove]
                    
                    rewound_context = current_context.create_next_step()
                    await context_service.store_context(rewound_context, db)
                    
                    execution_inputs["_context"] = rewound_context.context_id
                
            elif context_action.action == "new":
                if context_action.conversation_id:
                    execution_inputs["_conversation_id"] = context_action.conversation_id
        
        # Get graph from database by ID or name
        db_graph = resolve_graph_by_id_or_name(request.graph_id, db)
        
        # Convert to internal format
        nodes = {}
        for node_id, node_data in db_graph.nodes.items():
            nodes[node_id] = NodeData(
                node_id=node_data["node_id"],
                node_type=node_data["node_type"],
                position=node_data.get("position", {}),
                parameters=node_data.get("parameters", {}),
                label=node_data.get("label"),
                description=node_data.get("description")
            )
            
        edges = []
        for edge_data in db_graph.edges:
            edges.append(EdgeData(
                edge_id=edge_data["edge_id"],
                source_node=edge_data["source_node"],
                source_port=edge_data["source_port"],
                target_node=edge_data["target_node"],
                target_port=edge_data["target_port"]
            ))
        
        graph_data = GraphData(
            graph_id=str(db_graph.id),
            name=db_graph.name,
            nodes=nodes,
            edges=edges,
            meta_data=db_graph.meta_data
        )
        
        # Create execution record
        db_execution = Execution(
            graph_id=db_graph.id,
            status="running",
            inputs=execution_inputs
        )
        db.add(db_execution)
        db.commit()
        db.refresh(db_execution)
        
        # Get node registry from app state
        node_registry = fastapi_request.app.state.node_registry
        
        async def streaming_generator():
            """Generate Server-Sent Events for streaming execution."""
            try:
                # Execute graph with streaming
                executor = GraphExecutor(node_registry.get_all())
                
                async for event in executor.execute_graph_streaming(graph_data, execution_inputs):
                    # Send as Server-Sent Event
                    yield f"data: {json.dumps(event)}\n\n"
                
                # Update execution record with final status
                db_execution.status = "completed"
                db.commit()
                
            except Exception as e:
                logger.error(f"Streaming execution failed: {e}")
                
                # Send error event
                error_event = {
                    "type": "execution_error",
                    "error": str(e),
                    "timestamp": datetime.utcnow().isoformat()
                }
                yield f"data: {json.dumps(error_event)}\n\n"
                
                # Update execution record with error
                db_execution.status = "failed"
                db_execution.errors = {"execution": str(e)}
                db.commit()
        
        return StreamingResponse(
            streaming_generator(),
            media_type="text/plain",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Content-Type": "text/event-stream"
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to start streaming execution: {e}")
        raise HTTPException(status_code=500, detail=str(e))