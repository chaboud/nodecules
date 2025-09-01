"""Core types for the nodecules execution engine."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from uuid import uuid4


class NodeStatus(str, Enum):
    """Node execution status."""
    PENDING = "pending"
    RUNNING = "running" 
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class DataType(str, Enum):
    """Supported data types."""
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    JSON = "json"
    FILE = "file"
    CONTEXT = "context"
    ANY = "any"


@dataclass
class PortSpec:
    """Specification for node input/output port."""
    name: str
    data_type: DataType
    required: bool = True
    default: Any = None
    description: str = ""


@dataclass
class ParameterSpec:
    """Specification for node parameter."""
    name: str
    data_type: str
    default: Any = None
    description: str = ""
    constraints: Optional[Dict[str, Any]] = None


@dataclass
class ResourceRequirement:
    """Resource requirements for node execution."""
    cpu_cores: float = 1.0
    memory_mb: int = 512
    gpu_count: int = 0
    timeout_seconds: int = 300


@dataclass 
class NodeSpec:
    """Node type specification."""
    node_type: str
    display_name: str
    description: str
    category: str = "General"
    inputs: List[PortSpec] = field(default_factory=list)
    outputs: List[PortSpec] = field(default_factory=list)
    parameters: List[ParameterSpec] = field(default_factory=list)
    resource_requirements: ResourceRequirement = field(default_factory=ResourceRequirement)


@dataclass
class NodeData:
    """Runtime node data."""
    node_id: str
    node_type: str
    position: Dict[str, float] = field(default_factory=dict)
    parameters: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        if not self.node_id:
            self.node_id = str(uuid4())


@dataclass
class EdgeData:
    """Edge connection data."""
    edge_id: str
    source_node: str
    source_port: str
    target_node: str
    target_port: str
    
    def __post_init__(self):
        if not self.edge_id:
            self.edge_id = f"{self.source_node}_{self.source_port}-{self.target_node}_{self.target_port}"


@dataclass
class GraphData:
    """Complete graph definition."""
    graph_id: str
    name: str = "Untitled Graph"
    nodes: Dict[str, NodeData] = field(default_factory=dict)
    edges: List[EdgeData] = field(default_factory=list)
    meta_data: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    
    def __post_init__(self):
        if not self.graph_id:
            self.graph_id = str(uuid4())


@dataclass
class ExecutionContext:
    """Runtime execution context."""
    execution_id: str
    graph: GraphData
    execution_inputs: Dict[str, Any] = field(default_factory=dict)
    node_outputs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    node_status: Dict[str, NodeStatus] = field(default_factory=dict)
    errors: Dict[str, str] = field(default_factory=dict)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    def __post_init__(self):
        if not self.execution_id:
            self.execution_id = str(uuid4())
            
    def get_input_value(self, node_id: str, port_name: str) -> Any:
        """Get input value for a node port from connected outputs."""
        # Find the edge that connects to this input
        for edge in self.graph.edges:
            if edge.target_node == node_id and edge.target_port == port_name:
                # Get the output from the source node
                source_outputs = self.node_outputs.get(edge.source_node, {})
                return source_outputs.get(edge.source_port)
        return None
        
    def set_node_output(self, node_id: str, port_name: str, value: Any) -> None:
        """Set output value for a node port."""
        if node_id not in self.node_outputs:
            self.node_outputs[node_id] = {}
        self.node_outputs[node_id][port_name] = value
        
    def set_node_status(self, node_id: str, status: NodeStatus) -> None:
        """Set node execution status."""
        self.node_status[node_id] = status


class BaseNode(ABC):
    """Abstract base class for all node types."""
    
    def __init__(self, spec: NodeSpec):
        self.spec = spec
        
    @abstractmethod
    async def execute(self, context: ExecutionContext, node_data: NodeData) -> Dict[str, Any]:
        """Execute the node logic and return outputs."""
        pass
        
    def validate_inputs(self, inputs: Dict[str, Any]) -> bool:
        """Validate input data before execution."""
        for port in self.spec.inputs:
            if port.required and port.name not in inputs:
                return False
        return True
        
    def get_resource_requirements(self, parameters: Dict[str, Any]) -> ResourceRequirement:
        """Get resource requirements for execution."""
        return self.spec.resource_requirements