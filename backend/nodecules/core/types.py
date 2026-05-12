"""Core types for the nodecules execution engine."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union
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
    TIME_RANGE = "time_range"
    ANY = "any"


class DerivationPhase(str, Enum):
    """Phase tag on derivation events (PR-n6+).

    `WARMUP`: emitted while a filter is within settling time from a state
    change (cold start, processor swap, op-graph swap). Mathematically
    correct outputs but may not reflect steady-state response yet.
    Consumers willing to take best-effort early output (live display,
    progress UIs) opt in; default subscribers exclude these.

    `CANONICAL`: emitted after settling. The trusted output. Default
    visibility includes this phase.

    `SUPERSEDED`: previously-canonical output that's been replaced by a
    later cook (annotation re-anneal, op-graph swap). Kept for readers
    holding earlier roots; new subscribers don't see by default.
    """
    WARMUP = "warmup"
    CANONICAL = "canonical"
    SUPERSEDED = "superseded"


# Temporal-scheduling literals (see TEMPORALITY.md). Declared as Literals
# rather than Enums so dataclass defaults stay trivially JSON-serializable.
TemporalKind = Literal["static", "windowed", "streaming", "reanneal"]
EmitPolicy = Literal["streaming", "on_window_close", "on_graph_close"]
Mutability = Literal["wet", "drying", "dry", "smudged"]
WindowAlignment = Literal["origin", "boundary"]


@dataclass
class WindowSpec:
    """Window cadence for a `temporal_kind == "windowed"` node.

    All times are integer milliseconds. `stride_ms < size_ms` produces
    overlapping windows. `min_upstream_coverage` is the fraction of the
    window that must have upstream data before the node is considered
    ready to run for that window.
    """
    size_ms: int
    stride_ms: int
    align: WindowAlignment = "origin"
    min_upstream_coverage: float = 1.0

    def __post_init__(self) -> None:
        if self.size_ms <= 0:
            raise ValueError(f"WindowSpec size_ms must be > 0, got {self.size_ms}")
        if self.stride_ms <= 0:
            raise ValueError(f"WindowSpec stride_ms must be > 0, got {self.stride_ms}")
        if not (0.0 <= self.min_upstream_coverage <= 1.0):
            raise ValueError(
                f"WindowSpec min_upstream_coverage must be in [0.0, 1.0], got {self.min_upstream_coverage}"
            )


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
    """Node type specification.

    All non-original fields default to values that reproduce pre-temporality
    behavior. Existing nodes do not need to be modified.

    PR-n4 added: is_deterministic, reads_strips, writes_strips.
    PR-n6 added: (DerivationPhase enum, no NodeSpec change).
    PR-n7 added: settling_windows.
    PR-n8 added: reads_env, writes_env.
    """
    node_type: str
    display_name: str
    description: str
    category: str = "General"
    inputs: List[PortSpec] = field(default_factory=list)
    outputs: List[PortSpec] = field(default_factory=list)
    parameters: List[ParameterSpec] = field(default_factory=list)
    resource_requirements: ResourceRequirement = field(default_factory=ResourceRequirement)
    # --- Temporal scheduling (feat/temporality). See TEMPORALITY.md. ---
    temporal_kind: TemporalKind = "static"
    window_spec: Optional[WindowSpec] = None
    emit_policy: EmitPolicy = "on_window_close"
    supports_reanneal: bool = False
    # --- Determinism + strip binding (PR-n4). ---
    is_deterministic: bool = True
    reads_strips: List[str] = field(default_factory=list)
    writes_strips: List[str] = field(default_factory=list)
    # --- Environment / capability binding (PR-n8). ---
    reads_env: List[str] = field(default_factory=list)
    writes_env: List[str] = field(default_factory=list)
    # --- IIR / pre-roll (PR-n7). ---
    # Number of windows of upstream evidence this node consumes before its
    # outputs are trustworthy. Default 0 = no settling time, output
    # canonical from window 0. The scheduler-v2 cooker uses this to compute
    # pre-roll depth (see TEMPORALITY-ROADMAP.md § PR-n7).
    settling_windows: int = 0


@dataclass
class NodeData:
    """Runtime node data."""
    node_id: str
    node_type: str
    position: Dict[str, float] = field(default_factory=dict)
    parameters: Dict[str, Any] = field(default_factory=dict)
    label: Optional[str] = None
    description: Optional[str] = None

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
        for edge in self.graph.edges:
            if edge.target_node == node_id and edge.target_port == port_name:
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
