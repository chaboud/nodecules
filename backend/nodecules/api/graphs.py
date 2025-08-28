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


def resolve_graph_by_id_or_name(graph_identifier: str, db: Session) -> Graph:
    """Helper function to find a graph by UUID or name."""
    graph = None
    
    # Try to parse as UUID first
    try:
        graph_uuid = UUID(graph_identifier)
        graph = db.query(Graph).filter(Graph.id == graph_uuid).first()
    except ValueError:
        # Not a valid UUID, try to find by name (case-insensitive first)
        graph = db.query(Graph).filter(Graph.name.ilike(graph_identifier)).first()
        
        # If not found, try exact case-sensitive match
        if not graph:
            graph = db.query(Graph).filter(Graph.name == graph_identifier).first()
    
    if not graph:
        raise HTTPException(
            status_code=404,
            detail=f"Graph not found: {graph_identifier}"
        )
    
    return graph


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


@router.get("/{graph_identifier}", response_model=GraphResponse)
async def get_graph(
    graph_identifier: str,
    db: Session = Depends(get_database)
):
    """Get a graph by ID or name."""
    graph = resolve_graph_by_id_or_name(graph_identifier, db)
    return GraphResponse.model_validate(graph)


@router.get("/", response_model=List[GraphResponse])
async def list_graphs(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_database)
):
    """List all graphs."""
    graphs = db.query(Graph).offset(skip).limit(limit).all()
    return [GraphResponse.model_validate(graph) for graph in graphs]


@router.put("/{graph_identifier}", response_model=GraphResponse)
async def update_graph(
    graph_identifier: str,
    request: GraphUpdateRequest,
    db: Session = Depends(get_database)
):
    """Update a graph by ID or name."""
    db_graph = resolve_graph_by_id_or_name(graph_identifier, db)
    
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


@router.delete("/{graph_identifier}")
async def delete_graph(
    graph_identifier: str,
    db: Session = Depends(get_database)
):
    """Delete a graph by ID or name."""
    db_graph = resolve_graph_by_id_or_name(graph_identifier, db)
    
    db.delete(db_graph)
    db.commit()
    
    logger.info(f"Deleted graph {db_graph.id}")
    return {"message": "Graph deleted successfully"}


@router.post("/{graph_identifier}/copy", response_model=GraphResponse)
async def copy_graph(
    graph_identifier: str,
    db: Session = Depends(get_database)
):
    """Create a copy of an existing graph by ID or name."""
    # Get the original graph
    original_graph = resolve_graph_by_id_or_name(graph_identifier, db)
    
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
    
    logger.info(f"Copied graph {original_graph.id} to new graph {new_graph.id}")
    return GraphResponse.model_validate(new_graph)


@router.get("/{graph_identifier}/schema")
async def get_graph_schema(
    graph_identifier: str,
    db: Session = Depends(get_database)
):
    """Get the input/output schema for a graph by ID or name with friendly names."""
    graph = resolve_graph_by_id_or_name(graph_identifier, db)
    
    # Extract input nodes with their friendly information
    inputs = []
    input_nodes = [(node_id, node_data) for node_id, node_data in graph.nodes.items() 
                   if node_data.get("node_type") == "input"]
    
    # Sort by node_id for consistent ordinal ordering
    input_nodes.sort(key=lambda x: x[0])
    
    for i, (node_id, node_data) in enumerate(input_nodes, 1):
        parameters = node_data.get("parameters", {})
        label = parameters.get("label", "").strip()
        default_value = parameters.get("value", "")
        data_type = parameters.get("data_type", "text")
        
        input_info = {
            "node_id": node_id,
            "ordinal": i,
            "ordinal_key": f"input_{i}",
            "label": label if label else None,
            "data_type": data_type,
            "default_value": default_value,
            "description": f"Input {i}" + (f" ({label})" if label else "")
        }
        inputs.append(input_info)
    
    # Extract output nodes
    outputs = []
    output_nodes = [(node_id, node_data) for node_id, node_data in graph.nodes.items() 
                    if node_data.get("node_type") == "output"]
    
    for node_id, node_data in output_nodes:
        parameters = node_data.get("parameters", {})
        output_label = parameters.get("label", "Output")
        
        output_info = {
            "node_id": node_id,
            "label": output_label,
            "result_key": "result",  # Output nodes return their result in "result" field
            "description": f"Output: {output_label}"
        }
        outputs.append(output_info)
    
    return {
        "graph_id": str(graph.id),
        "graph_name": graph.name,
        "inputs": inputs,
        "outputs": outputs,
        "example_call": {
            "url": f"/api/v1/executions/",
            "method": "POST",
            "body": {
                "graph_id": str(graph.id),
                "inputs": {
                    **{inp["ordinal_key"]: f"<{inp['description']}>" for inp in inputs},
                    **{inp["label"]: f"<{inp['description']}>" for inp in inputs if inp["label"]}
                }
            }
        }
    }