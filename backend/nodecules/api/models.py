"""Pydantic models for API requests and responses."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID
from pydantic import BaseModel, Field


class NodeDataRequest(BaseModel):
    """Node data in API requests."""
    node_id: str
    node_type: str
    position: Dict[str, float] = Field(default_factory=dict)
    parameters: Dict[str, Any] = Field(default_factory=dict)


class EdgeDataRequest(BaseModel):
    """Edge data in API requests."""
    edge_id: str
    source_node: str
    source_port: str
    target_node: str
    target_port: str


class GraphCreateRequest(BaseModel):
    """Request to create a new graph."""
    name: str
    description: Optional[str] = None
    nodes: Dict[str, NodeDataRequest] = Field(default_factory=dict)
    edges: List[EdgeDataRequest] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class GraphUpdateRequest(BaseModel):
    """Request to update a graph."""
    name: Optional[str] = None
    description: Optional[str] = None
    nodes: Optional[Dict[str, NodeDataRequest]] = None
    edges: Optional[List[EdgeDataRequest]] = None
    metadata: Optional[Dict[str, Any]] = None


class GraphResponse(BaseModel):
    """Graph response model."""
    id: UUID
    name: str
    description: Optional[str]
    nodes: Dict[str, Any]
    edges: List[Any]
    metadata: Dict[str, Any]
    created_at: datetime
    updated_at: datetime
    created_by: Optional[str]

    class Config:
        from_attributes = True
        
    @classmethod
    def model_validate(cls, obj):
        # Handle the meta_data -> metadata mapping
        if hasattr(obj, 'meta_data'):
            data = {
                'id': obj.id,
                'name': obj.name,
                'description': obj.description,
                'nodes': obj.nodes,
                'edges': obj.edges,
                'metadata': obj.meta_data,  # Map meta_data to metadata
                'created_at': obj.created_at,
                'updated_at': obj.updated_at,
                'created_by': obj.created_by,
            }
            return cls(**data)
        return super().model_validate(obj)


class ExecutionCreateRequest(BaseModel):
    """Request to execute a graph."""
    graph_id: str  # Can be UUID or graph name
    inputs: Dict[str, Any] = Field(default_factory=dict)


class ExecutionResponse(BaseModel):
    """Execution response model."""
    id: UUID
    graph_id: UUID
    status: str
    inputs: Dict[str, Any]
    outputs: Dict[str, Any]
    node_status: Dict[str, Any]
    errors: Dict[str, Any]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


class PortSpecResponse(BaseModel):
    """Port specification response."""
    name: str
    data_type: str
    required: bool = True
    default: Any = None
    description: str = ""


class ParameterSpecResponse(BaseModel):
    """Parameter specification response."""
    name: str
    data_type: str
    default: Any = None
    description: str = ""
    constraints: Optional[Dict[str, Any]] = None


class NodeSpecResponse(BaseModel):
    """Node specification response."""
    node_type: str
    display_name: str
    description: str
    category: str = "General"
    inputs: List[PortSpecResponse] = Field(default_factory=list)
    outputs: List[PortSpecResponse] = Field(default_factory=list)
    parameters: List[ParameterSpecResponse] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    """Error response model."""
    error: str
    detail: Optional[str] = None
    errors: Optional[List[str]] = None