"""API routes for graph execution."""

import logging
from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..models.database import get_database
from ..models.schemas import Graph, Execution
from ..core.types import GraphData, NodeData, EdgeData
from ..core.executor import GraphExecutor
from .models import ExecutionCreateRequest, ExecutionResponse, ErrorResponse

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/", response_model=ExecutionResponse)
async def execute_graph(
    request: ExecutionCreateRequest,
    fastapi_request: Request,
    db: Session = Depends(get_database)
):
    """Execute a graph."""
    try:
        # Get graph from database
        db_graph = db.query(Graph).filter(Graph.id == request.graph_id).first()
        if not db_graph:
            raise HTTPException(status_code=404, detail="Graph not found")
        
        # Convert to internal format
        nodes = {}
        for node_id, node_data in db_graph.nodes.items():
            nodes[node_id] = NodeData(
                node_id=node_data["node_id"],
                node_type=node_data["node_type"],
                position=node_data.get("position", {}),
                parameters=node_data.get("parameters", {})
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
            graph_id=request.graph_id,
            status="pending",
            inputs=request.inputs
        )
        db.add(db_execution)
        db.commit()
        db.refresh(db_execution)
        
        try:
            # Get node registry from app state
            node_registry = fastapi_request.app.state.node_registry
            
            # Execute graph
            executor = GraphExecutor(node_registry.get_all())
            context = await executor.execute_graph(graph_data, request.inputs)
            
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