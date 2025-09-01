# Backend Architecture - Nodecules

The Nodecules backend is a Python FastAPI-based application that provides the core graph execution engine, API services, and data management layer.

## Directory Structure

```
backend/
├── nodecules/                    # Main application package
│   ├── __init__.py
│   ├── main.py                   # FastAPI application entry point
│   ├── api/                      # REST API routes
│   │   ├── executions.py         # Graph execution endpoints
│   │   ├── graphs.py             # Graph CRUD operations
│   │   ├── instances.py          # Instance management
│   │   ├── plugins.py            # Plugin and node type APIs
│   │   └── models.py             # API request/response models
│   ├── core/                     # Core business logic
│   │   ├── executor.py           # Graph execution engine
│   │   ├── graph.py              # Graph processing logic
│   │   ├── types.py              # Core data types and interfaces
│   │   ├── instance_executor.py  # Instance execution logic
│   │   ├── smart_context.py      # AI context management
│   │   └── content_addressable_context.py  # Immutable contexts
│   ├── models/                   # Data layer
│   │   ├── database.py           # Database configuration
│   │   ├── schemas.py            # SQLAlchemy models
│   │   └── instance.py           # Instance-related models
│   ├── plugins/                  # Node implementations
│   │   ├── builtin_nodes.py      # Core built-in nodes
│   │   ├── smart_chat_node.py    # Smart AI chat nodes
│   │   ├── immutable_chat_node.py # Immutable AI chat nodes
│   │   ├── graph_nodes.py        # Graph-as-node functionality
│   │   └── loader.py             # Plugin loading system
│   └── services/                 # External service integrations
├── plugins/                      # External plugin directory
├── alembic/                      # Database migrations
│   ├── versions/                 # Migration files
│   └── env.py                    # Alembic configuration
├── tests/                        # Test suites
├── Dockerfile                    # Container configuration
└── pyproject.toml                # Project dependencies and config
```

## Core Architecture Components

### 1. Application Entry Point (`main.py`)

The FastAPI application with lifecycle management:

```python
# Global plugin manager and node registry
plugin_manager: PluginManager = None
node_registry: NodeRegistry = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize plugin system
    plugin_manager = PluginManager(["./plugins"])
    plugin_manager.initialize()
    
    # Initialize node registry with built-in nodes
    node_registry = NodeRegistry()
    for node_type, node_class in BUILTIN_NODES.items():
        node_registry.register(node_type, node_class)
```

**Key Features:**
- Plugin system initialization on startup
- Node registry population
- CORS middleware for frontend communication
- Structured logging configuration

### 2. API Layer (`api/`)

RESTful API endpoints organized by domain:

#### Graph Management (`graphs.py`)
```python
@router.post("/", response_model=GraphResponse)
async def create_graph(request: GraphCreateRequest, db: Session = Depends(get_database))

@router.get("/{graph_identifier}", response_model=GraphResponse)
async def get_graph(graph_identifier: str, db: Session = Depends(get_database))

@router.post("/{graph_identifier}/execute", response_model=GraphExecuteResponse)
async def execute_graph(graph_identifier: str, request: GraphExecuteRequest, db: Session = Depends(get_database))
```

**Features:**
- Graph CRUD operations (Create, Read, Update, Delete)
- Graph execution by ID or name
- Graph copying and schema introspection
- Flexible input resolution (labels, ordinals, node IDs)

#### Execution Management (`executions.py`)
- Execution history tracking
- Real-time execution monitoring
- Streaming execution support
- Execution status management

#### Instance Management (`instances.py`)
- Persistent graph instances for multi-run execution
- Instance state management
- Cross-execution data persistence

#### Plugin Management (`plugins.py`)
- Available node type discovery
- Node specification retrieval
- Plugin metadata access

### 3. Core Business Logic (`core/`)

#### Execution Engine (`executor.py`)

The heart of the graph processing system:

```python
class GraphExecutor:
    def __init__(self, node_registry: Dict[str, type[BaseNode]]):
        self.node_registry = node_registry
        
    async def execute_graph(self, graph: GraphData, inputs: Optional[Dict[str, Any]] = None) -> ExecutionContext:
        # Initialize execution context
        # Plan execution order via topological sort
        # Execute nodes sequentially or in parallel
        # Handle errors and collect results
```

**Execution Strategies:**
- **Sequential**: Nodes executed in dependency order
- **Parallel Batches**: Independent nodes executed concurrently
- **Streaming**: Real-time output for compatible nodes

#### Graph Processing (`graph.py`)

Graph analysis and planning:

```python
class GraphExecutionPlanner:
    def get_execution_order(self) -> List[str]:
        # Topological sort for dependency resolution
        
    def get_parallel_batches(self) -> List[List[str]]:
        # Group independent nodes for parallel execution
        
    def validate_graph(self) -> List[str]:
        # Check for cycles, missing nodes, invalid connections
```

#### Type System (`types.py`)

Core data structures and interfaces:

```python
class BaseNode(ABC):
    @abstractmethod
    async def execute(self, context: ExecutionContext, node_data: NodeData) -> Dict[str, Any]:
        pass

@dataclass
class ExecutionContext:
    execution_id: str
    graph: GraphData
    execution_inputs: Dict[str, Any]
    node_outputs: Dict[str, Dict[str, Any]]
    node_status: Dict[str, NodeStatus]
    errors: Dict[str, str]
```

**Key Types:**
- `NodeSpec`: Node type specification with ports and parameters
- `GraphData`: Complete graph definition with nodes and edges
- `ExecutionContext`: Runtime execution state and data
- `NodeData`: Individual node configuration
- `EdgeData`: Connection between node ports

#### Context Management (`smart_context.py`, `content_addressable_context.py`)

Two sophisticated context management systems for AI integration:

**Smart Context:**
- Provider-aware context management
- Adapts to LLM capabilities (caching, context length)
- Optimizes for different providers (Anthropic, OpenAI, Ollama)

**Immutable Context:**
- Content-addressable storage for conversation contexts
- Efficient memory usage through deduplication
- Cryptographic hashing for context identification

### 4. Data Layer (`models/`)

#### Database Configuration (`database.py`)
```python
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://nodecules:nodecules@localhost:5432/nodecules")
engine = create_engine(DATABASE_URL, echo=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
```

#### Data Models (`schemas.py`)

SQLAlchemy models for persistent storage:

```python
class Graph(Base):
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    nodes = Column(JSON, nullable=False, default=dict)
    edges = Column(JSON, nullable=False, default=list)
    meta_data = Column(JSON, default=dict)
    executions = relationship("Execution", back_populates="graph")

class Execution(Base):
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    graph_id = Column(UUID(as_uuid=True), ForeignKey("graphs.id"))
    status = Column(String(50), default="pending")
    inputs = Column(JSON, default=dict)
    outputs = Column(JSON, default=dict)
    node_status = Column(JSON, default=dict)
    errors = Column(JSON, default=dict)
```

**Database Schema:**
- `graphs`: Graph definitions and metadata
- `executions`: Execution history and results
- `graph_instances`: Persistent instances for multi-run execution
- `smart_contexts`: AI context management
- `data_objects`: Media and file storage
- `users`: User management (basic implementation)

### 5. Node System (`plugins/`)

#### Built-in Nodes (`builtin_nodes.py`)

Core processing nodes:

```python
class InputNode(BaseNode):
    async def execute(self, context: ExecutionContext, node_data: NodeData) -> Dict[str, Any]:
        # Multi-strategy input resolution:
        # 1. By label (user-friendly names)
        # 2. By ordinal (input_1, input_2, ...)
        # 3. By node ID (backwards compatibility)
        # 4. From parameters (default values)

class TextTransformNode(BaseNode):
    # Text processing: uppercase, lowercase, strip, reverse
    
class JsonExtractNode(BaseNode):
    # JSON field extraction with dot notation support

class OutputNode(BaseNode):
    # Result collection and display
```

**Available Built-in Nodes:**
- **I/O Nodes**: `input`, `output`
- **Text Processing**: `text_transform`, `text_filter`, `text_concat`
- **Data Processing**: `json_extract`, `json_replace`
- **AI Nodes**: `smart_chat`, `immutable_chat`
- **Graph Nodes**: `sub_graph` (graphs as reusable components)

#### AI Chat Nodes

**Smart Chat Node:**
- Provider-aware context management
- Automatic context optimization based on LLM capabilities
- Support for streaming responses
- Integration with smart context system

**Immutable Chat Node:**
- Content-addressable context storage
- Efficient memory usage through deduplication
- Cryptographic context identification
- Optimal for large-scale conversation management

#### Plugin System (`loader.py`)

Dynamic plugin loading system:

```python
class PluginManager:
    def __init__(self, plugin_dirs: List[str]):
        self.plugin_dirs = plugin_dirs
        self.loaded_plugins: Dict[str, dict] = {}
        self.node_classes: Dict[str, Type[BaseNode]] = {}
    
    def discover_plugins(self) -> List[PluginManifest]:
        # Scan plugin directories for plugin.yaml files
        
    def load_plugin(self, manifest: PluginManifest) -> bool:
        # Load Python modules and extract node classes
```

**Plugin Structure:**
```
plugins/example_plugin/
├── plugin.yaml          # Plugin metadata
├── example_plugin.py    # Node implementations
└── requirements.txt     # Dependencies (optional)
```

### 6. Database Migrations (`alembic/`)

Schema versioning with Alembic:

- `versions/1ac44db2a12e_add_smart_contexts_table.py`: Smart context storage
- `versions/e6b1e9e7359f_add_immutable_contexts_table.py`: Immutable context storage

## API Design Patterns

### 1. Resource-Based URLs
- `/api/v1/graphs/` - Graph collection
- `/api/v1/graphs/{id}` - Individual graph
- `/api/v1/executions/` - Execution collection
- `/api/v1/instances/{id}` - Graph instances

### 2. Flexible Identifiers
Many endpoints accept either UUID or name:
```python
def resolve_graph_by_id_or_name(graph_identifier: str, db: Session) -> Graph:
    # Try UUID first, then case-insensitive name matching
```

### 3. Comprehensive Error Handling
```python
try:
    # Operation logic
except Exception as e:
    logger.error(f"Operation failed: {e}")
    raise HTTPException(status_code=500, detail=str(e))
```

### 4. Dependency Injection
SQLAlchemy sessions and other dependencies injected via FastAPI:
```python
async def create_graph(request: GraphCreateRequest, db: Session = Depends(get_database)):
```

## Execution Flow

1. **Graph Loading**: Graph retrieved from database by ID/name
2. **Context Initialization**: ExecutionContext created with inputs
3. **Execution Planning**: Topological sort determines node order
4. **Node Execution**: Sequential or parallel execution based on strategy
5. **Data Flow**: Port-to-port data transmission between nodes
6. **Result Collection**: Final outputs and node states collected
7. **State Persistence**: Execution history and instance state saved

## Performance Considerations

### Asynchronous Processing
- Full async/await support throughout the codebase
- Non-blocking I/O operations for database and external services
- Concurrent node execution where dependencies allow

### Context Optimization
- Smart context system adapts to provider capabilities
- Immutable context system provides efficient memory usage
- Caching strategies reduce redundant computations

### Database Optimization
- Proper indexing on frequently queried columns
- JSON column usage for flexible schema requirements
- Connection pooling for concurrent request handling

## Error Handling and Logging

### Structured Logging
```python
import logging
logger = logging.getLogger(__name__)

# Throughout the codebase
logger.info(f"Executing graph {graph_id} with {len(nodes)} nodes")
logger.error(f"Node {node_id} failed: {error_message}")
logger.debug(f"Node {node_id} inputs: {inputs}")
```

### Exception Management
- Custom exception types for different error categories
- Detailed error tracking per node during execution
- Graceful degradation for non-critical failures

## Security Implementation

### Input Validation
- Pydantic models for comprehensive request validation
- SQLAlchemy parameterized queries prevent injection
- Type checking throughout the codebase

### Access Control
- Prepared for authentication integration
- User tracking in database models
- Plugin sandboxing considerations

## Testing Strategy

### Unit Testing
- Individual node testing with mock contexts
- Core algorithm testing (topological sort, execution planning)
- Database model validation

### Integration Testing
- End-to-end graph execution testing
- API endpoint testing
- Plugin loading and execution testing

This backend architecture provides a robust, scalable, and extensible foundation for the Nodecules graph processing system, with clear separation of concerns and comprehensive error handling.