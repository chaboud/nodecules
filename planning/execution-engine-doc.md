# Execution Engine Architecture

## Overview

The Execution Engine is the heart of the node-based pipeline system, responsible for orchestrating the execution of user-defined workflows. It implements a sophisticated graph execution model inspired by ComfyUI's topological sort approach, with enhancements for distributed processing, resource management, and error recovery.

## Core Concepts

### Node Definition

```python
class NodeDefinition:
    """Base node structure for all pipeline nodes"""
    
    node_id: str                    # Unique identifier
    node_type: str                   # Node class/type
    inputs: Dict[str, InputPort]     # Input connections
    outputs: Dict[str, OutputPort]   # Output definitions
    parameters: Dict[str, Any]       # Node configuration
    metadata: NodeMetadata           # Execution metadata
    
class InputPort:
    name: str
    type: DataType
    required: bool
    default: Optional[Any]
    validator: Optional[Callable]
    
class OutputPort:
    name: str
    type: DataType
    cacheable: bool
    broadcast: bool
```

### Graph Structure

```python
class ExecutionGraph:
    """Directed Acyclic Graph representation"""
    
    nodes: Dict[str, NodeDefinition]
    edges: List[Edge]
    metadata: GraphMetadata
    execution_order: Optional[List[str]]  # Cached topological sort
    
class Edge:
    source_node: str
    source_port: str
    target_node: str
    target_port: str
    transform: Optional[DataTransform]
```

## Execution Model

### Topological Sort Implementation

```python
def topological_sort_kahn(graph: ExecutionGraph) -> List[str]:
    """
    Kahn's algorithm for topological sorting
    Returns execution order or raises if cycle detected
    """
    in_degree = calculate_in_degrees(graph)
    queue = deque([node for node, degree in in_degree.items() if degree == 0])
    execution_order = []
    
    while queue:
        node = queue.popleft()
        execution_order.append(node)
        
        for successor in graph.get_successors(node):
            in_degree[successor] -= 1
            if in_degree[successor] == 0:
                queue.append(successor)
    
    if len(execution_order) != len(graph.nodes):
        raise CyclicGraphError("Graph contains cycles")
    
    return execution_order
```

### Execution Strategies

#### 1. Sequential Execution
- Single-threaded execution following topological order
- Lowest resource usage
- Best for debugging and simple workflows

#### 2. Parallel Execution
- Thread pool or process pool based
- Executes independent nodes simultaneously
- Resource limits prevent oversubscription

#### 3. Distributed Execution
- Celery-based task distribution
- Kubernetes job scheduling
- AWS Lambda for serverless execution

#### 4. Lazy Execution
- Nodes execute only when outputs are needed
- Backward traversal from requested outputs
- Optimal for exploratory workflows

### Execution Context

```python
class ExecutionContext:
    """Runtime context for node execution"""
    
    execution_id: str
    graph: ExecutionGraph
    cache: CacheManager
    resources: ResourceManager
    logger: Logger
    metrics: MetricsCollector
    
    # Runtime state
    node_outputs: Dict[str, Any]
    execution_state: Dict[str, NodeState]
    error_handler: ErrorHandler
    
    def get_input(self, node_id: str, port_name: str) -> Any:
        """Retrieve input value from connected node output"""
        
    def set_output(self, node_id: str, port_name: str, value: Any):
        """Store node output for downstream consumption"""
        
    def checkpoint(self):
        """Save execution state for recovery"""
```

## Resource Management

### Resource Types

```python
class ResourceRequirement:
    cpu_cores: float = 1.0
    memory_mb: int = 512
    gpu_count: int = 0
    gpu_memory_mb: int = 0
    disk_space_mb: int = 100
    network_bandwidth_mbps: float = 10.0
    
class ResourcePool:
    available_cpu: float
    available_memory: int
    available_gpus: List[GPUResource]
    reserved_resources: Dict[str, ResourceRequirement]
```

### Resource Scheduling

```python
class ResourceScheduler:
    """Manages resource allocation for node execution"""
    
    def can_schedule(self, node: NodeDefinition) -> bool:
        """Check if node can be scheduled with available resources"""
        
    def reserve_resources(self, node: NodeDefinition) -> ResourceToken:
        """Reserve resources for node execution"""
        
    def release_resources(self, token: ResourceToken):
        """Release resources after node completion"""
        
    def wait_for_resources(self, requirement: ResourceRequirement) -> ResourceToken:
        """Block until resources become available"""
```

### Memory Management

```python
class MemoryManager:
    """Intelligent memory management for large data processing"""
    
    def offload_to_disk(self, data: Any, priority: int) -> DiskHandle:
        """Offload data to disk when memory pressure is high"""
        
    def pin_in_memory(self, data: Any) -> MemoryHandle:
        """Pin critical data in memory"""
        
    def get_memory_pressure(self) -> float:
        """Return current memory pressure (0.0 to 1.0)"""
        
    def cleanup_unused(self):
        """Garbage collect unused cached data"""
```

## Caching Strategy

### Cache Levels

```python
class CacheHierarchy:
    """Multi-level cache system"""
    
    l1_memory_cache: Dict[str, Any]      # In-process memory
    l2_redis_cache: Redis                # Distributed cache
    l3_disk_cache: DiskCache             # Local disk cache
    l4_s3_cache: S3Cache                 # Cloud object storage
    
    def get(self, key: str) -> Optional[Any]:
        """Retrieve from nearest cache level"""
        
    def set(self, key: str, value: Any, ttl: int = None):
        """Store in appropriate cache levels"""
```

### Cache Key Generation

```python
def generate_cache_key(node: NodeDefinition, inputs: Dict) -> str:
    """Generate deterministic cache key for node execution"""
    
    # Include node type, parameters, and input hashes
    key_components = [
        node.node_type,
        hash_dict(node.parameters),
        hash_inputs(inputs),
        CACHE_VERSION
    ]
    
    return hashlib.sha256(
        json.dumps(key_components, sort_keys=True).encode()
    ).hexdigest()
```

## Error Handling

### Error Types

```python
class NodeExecutionError(Exception):
    node_id: str
    error_type: ErrorType
    message: str
    stacktrace: str
    recoverable: bool
    
class ErrorType(Enum):
    VALIDATION_ERROR = "validation"
    RESOURCE_ERROR = "resource"
    TIMEOUT_ERROR = "timeout"
    DEPENDENCY_ERROR = "dependency"
    RUNTIME_ERROR = "runtime"
```

### Recovery Strategies

```python
class ErrorRecovery:
    """Error recovery strategies for node execution"""
    
    def retry_with_backoff(self, node: NodeDefinition, error: Exception) -> Any:
        """Exponential backoff retry strategy"""
        
    def use_fallback(self, node: NodeDefinition) -> Any:
        """Execute fallback node or return default value"""
        
    def skip_and_continue(self, node: NodeDefinition) -> None:
        """Skip failed node and continue workflow"""
        
    def checkpoint_and_halt(self, context: ExecutionContext):
        """Save state and halt execution for manual intervention"""
```

## Distributed Execution

### Task Distribution

```python
# Celery task definition
@celery_app.task(bind=True, max_retries=3)
def execute_node_task(self, node_data: dict, context_data: dict) -> dict:
    """Celery task for distributed node execution"""
    
    try:
        node = deserialize_node(node_data)
        context = deserialize_context(context_data)
        
        # Execute node
        result = node.execute(context)
        
        # Store result in distributed cache
        cache.set(f"result:{node.node_id}", result)
        
        return {"status": "success", "output": result}
        
    except Exception as exc:
        # Retry with exponential backoff
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)
```

### Coordination

```python
class DistributedCoordinator:
    """Coordinates distributed execution across workers"""
    
    def __init__(self, backend: str = "redis://localhost:6379"):
        self.celery = Celery('pipeline', broker=backend)
        self.lock_manager = Redlock([backend])
        
    def submit_graph(self, graph: ExecutionGraph) -> ExecutionHandle:
        """Submit graph for distributed execution"""
        
    def monitor_execution(self, handle: ExecutionHandle) -> ExecutionStatus:
        """Monitor distributed execution progress"""
        
    def cancel_execution(self, handle: ExecutionHandle):
        """Cancel all running tasks for execution"""
```

## Performance Optimization

### Execution Optimization

```python
class ExecutionOptimizer:
    """Optimizes graph execution for performance"""
    
    def fuse_nodes(self, graph: ExecutionGraph) -> ExecutionGraph:
        """Fuse compatible adjacent nodes for reduced overhead"""
        
    def parallelize_branches(self, graph: ExecutionGraph) -> ExecutionPlan:
        """Identify and parallelize independent branches"""
        
    def optimize_memory_layout(self, graph: ExecutionGraph):
        """Optimize data layout for cache locality"""
        
    def batch_similar_operations(self, nodes: List[NodeDefinition]):
        """Batch similar operations for GPU efficiency"""
```

### Profiling and Metrics

```python
class ExecutionProfiler:
    """Profile node execution for optimization"""
    
    def profile_node(self, node: NodeDefinition) -> NodeProfile:
        return NodeProfile(
            execution_time=timer.elapsed(),
            memory_usage=memory_tracker.peak(),
            cache_hits=cache.get_hit_rate(),
            io_operations=io_tracker.count()
        )
    
    def generate_report(self, execution_id: str) -> ProfilingReport:
        """Generate comprehensive profiling report"""
```

## Monitoring and Observability

### Metrics Collection

```python
# Prometheus metrics
execution_duration = Histogram(
    'node_execution_duration_seconds',
    'Node execution duration',
    ['node_type', 'status']
)

execution_counter = Counter(
    'node_executions_total',
    'Total node executions',
    ['node_type', 'status']
)

resource_usage = Gauge(
    'resource_usage_percent',
    'Resource usage percentage',
    ['resource_type']
)
```

### Distributed Tracing

```python
from opentelemetry import trace

tracer = trace.get_tracer(__name__)

class TracedExecution:
    """Add distributed tracing to execution"""
    
    def execute_node(self, node: NodeDefinition, context: ExecutionContext):
        with tracer.start_as_current_span(
            f"execute_node_{node.node_type}",
            attributes={
                "node.id": node.node_id,
                "node.type": node.node_type,
                "execution.id": context.execution_id
            }
        ) as span:
            try:
                result = node.execute(context)
                span.set_status(StatusCode.OK)
                return result
            except Exception as e:
                span.record_exception(e)
                span.set_status(StatusCode.ERROR)
                raise
```

## Testing Strategies

### Unit Testing

```python
def test_topological_sort():
    """Test topological sorting algorithm"""
    graph = create_test_graph()
    order = topological_sort_kahn(graph)
    
    # Verify all nodes are included
    assert len(order) == len(graph.nodes)
    
    # Verify dependencies are respected
    for edge in graph.edges:
        assert order.index(edge.source_node) < order.index(edge.target_node)
```

### Integration Testing

```python
def test_distributed_execution():
    """Test distributed execution with multiple workers"""
    graph = create_complex_graph()
    coordinator = DistributedCoordinator()
    
    handle = coordinator.submit_graph(graph)
    status = coordinator.monitor_execution(handle)
    
    assert status.completed_successfully()
    assert all(node.executed for node in graph.nodes.values())
```

## Configuration

```yaml
# execution_engine.yaml
execution:
  strategy: "parallel"  # sequential, parallel, distributed, lazy
  max_workers: 8
  timeout_seconds: 300
  retry_attempts: 3
  retry_delay: 1.0
  
resource_management:
  cpu_cores: 8
  memory_mb: 16384
  gpu_count: 1
  enable_offloading: true
  offload_threshold: 0.8
  
caching:
  enable: true
  ttl_seconds: 3600
  max_cache_size_mb: 4096
  cache_levels: ["memory", "redis", "disk", "s3"]
  
monitoring:
  enable_metrics: true
  enable_tracing: true
  trace_sampling_rate: 0.1
  metrics_port: 9090
```

## Migration from ComfyUI

### Key Differences

1. **Distributed Execution**: Native support for multi-machine execution
2. **Resource Management**: Explicit resource requirements and scheduling
3. **Enhanced Caching**: Multi-level cache hierarchy
4. **Error Recovery**: Sophisticated error handling and recovery
5. **Observability**: Built-in metrics and tracing

### Compatibility Layer

```python
class ComfyUICompatibilityLayer:
    """Compatibility layer for ComfyUI workflows"""
    
    def import_workflow(self, comfy_workflow: dict) -> ExecutionGraph:
        """Import ComfyUI workflow format"""
        
    def export_workflow(self, graph: ExecutionGraph) -> dict:
        """Export to ComfyUI workflow format"""
        
    def wrap_comfy_node(self, comfy_node: Any) -> NodeDefinition:
        """Wrap ComfyUI node in our node interface"""
```

## Next Steps

1. Implement [Node Development Guide](node-development-guide)
2. Configure [Resource Manager Component](resource-manager-doc)
3. Set up [Cache Layer Component](cache-layer-doc)
4. Review [Performance Optimization Guide](performance-optimization-guide)