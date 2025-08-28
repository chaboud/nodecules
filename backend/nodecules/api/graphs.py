"""API routes for graph management."""

import logging
from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..models.database import get_database
from ..models.schemas import Graph
from ..core.types import GraphData, NodeData, EdgeData
from .models import (
    GraphCreateRequest, 
    GraphUpdateRequest, 
    GraphResponse, 
    ErrorResponse
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/", response_model=GraphResponse)
async def create_graph(
    request: GraphCreateRequest,
    db: Session = Depends(get_database)
):
    """Create a new graph."""
    try:
        # Convert request to internal format
        nodes = {}
        for node_id, node_req in request.nodes.items():
            nodes[node_id] = NodeData(
                node_id=node_req.node_id,
                node_type=node_req.node_type,
                position=node_req.position,
                parameters=node_req.parameters
            ).__dict__
            
        edges = []
        for edge_req in request.edges:
            edges.append(EdgeData(
                edge_id=edge_req.edge_id,
                source_node=edge_req.source_node,
                source_port=edge_req.source_port,
                target_node=edge_req.target_node,
                target_port=edge_req.target_port
            ).__dict__)
        
        # Create database record
        db_graph = Graph(
            name=request.name,
            description=request.description,
            nodes=nodes,
            edges=edges,
            meta_data=request.metadata,
            created_by="system"  # TODO: Get from authentication
        )
        
        db.add(db_graph)
        db.commit()
        db.refresh(db_graph)
        
        logger.info(f"Created graph {db_graph.id}: {db_graph.name}")
        return GraphResponse.model_validate(db_graph)
        
    except Exception as e:
        logger.error(f"Failed to create graph: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{graph_id}", response_model=GraphResponse)
async def get_graph(
    graph_id: UUID,
    db: Session = Depends(get_database)
):
    """Get a graph by ID."""
    db_graph = db.query(Graph).filter(Graph.id == graph_id).first()
    if not db_graph:
        raise HTTPException(status_code=404, detail="Graph not found")
    
    return GraphResponse.model_validate(db_graph)


@router.get("/", response_model=List[GraphResponse])
async def list_graphs(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_database)
):
    """List all graphs."""
    graphs = db.query(Graph).offset(skip).limit(limit).all()
    return [GraphResponse.model_validate(graph) for graph in graphs]


@router.put("/{graph_id}", response_model=GraphResponse)
async def update_graph(
    graph_id: UUID,
    request: GraphUpdateRequest,
    db: Session = Depends(get_database)
):
    """Update a graph."""
    db_graph = db.query(Graph).filter(Graph.id == graph_id).first()
    if not db_graph:
        raise HTTPException(status_code=404, detail="Graph not found")
    
    # Update fields if provided
    if request.name is not None:
        db_graph.name = request.name
    if request.description is not None:
        db_graph.description = request.description
    if request.metadata is not None:
        db_graph.meta_data = request.metadata
        
    # Update nodes if provided
    if request.nodes is not None:
        nodes = {}
        for node_id, node_req in request.nodes.items():
            nodes[node_id] = NodeData(
                node_id=node_req.node_id,
                node_type=node_req.node_type,
                position=node_req.position,
                parameters=node_req.parameters
            ).__dict__
        db_graph.nodes = nodes
        
    # Update edges if provided
    if request.edges is not None:
        edges = []
        for edge_req in request.edges:
            edges.append(EdgeData(
                edge_id=edge_req.edge_id,
                source_node=edge_req.source_node,
                source_port=edge_req.source_port,
                target_node=edge_req.target_node,
                target_port=edge_req.target_port
            ).__dict__)
        db_graph.edges = edges
    
    db.commit()
    db.refresh(db_graph)
    
    logger.info(f"Updated graph {db_graph.id}")
    return GraphResponse.model_validate(db_graph)


@router.delete("/{graph_id}")
async def delete_graph(
    graph_id: UUID,
    db: Session = Depends(get_database)
):
    """Delete a graph."""
    db_graph = db.query(Graph).filter(Graph.id == graph_id).first()
    if not db_graph:
        raise HTTPException(status_code=404, detail="Graph not found")
    
    db.delete(db_graph)
    db.commit()
    
    logger.info(f"Deleted graph {graph_id}")
    return {"message": "Graph deleted successfully"}


@router.post("/{graph_id}/copy", response_model=GraphResponse)
async def copy_graph(
    graph_id: UUID,
    db: Session = Depends(get_database)
):
    """Create a copy of an existing graph."""
    # Get the original graph
    original_graph = db.query(Graph).filter(Graph.id == graph_id).first()
    if not original_graph:
        raise HTTPException(status_code=404, detail="Graph not found")
    
    # Create a new graph with copied data
    new_graph = Graph(
        name=f"{original_graph.name} (Copy)",
        description=original_graph.description,
        nodes=original_graph.nodes,
        edges=original_graph.edges,
        meta_data=original_graph.meta_data,
        created_by=original_graph.created_by
    )
    
    db.add(new_graph)
    db.commit()
    db.refresh(new_graph)
    
    logger.info(f"Copied graph {graph_id} to new graph {new_graph.id}")
    return GraphResponse.model_validate(new_graph)