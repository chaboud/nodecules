# Nodecules - System Architecture

Nodecules is a comprehensive node-based graph processing engine for building flexible AI-powered workflows with visual graph editing and conversational interfaces.

## Overview

The system consists of three primary components:
- **Backend**: Python FastAPI-based API server with graph execution engine
- **Frontend**: React-based visual graph editor with chat interface
- **Infrastructure**: PostgreSQL database, Redis cache, and containerized deployment

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Frontend (React)                         │
├─────────────────────┬─────────────────────┬─────────────────────┤
│    Graph Editor     │   Chat Interface    │   Graph Manager     │
│  - Visual editor    │  - Interactive UI   │  - CRUD operations  │
│  - Node palette     │  - Real-time exec   │  - List/copy graphs │
│  - Properties panel │  - Context mgmt     │  - Export/import    │
└─────────────────────┴─────────────────────┴─────────────────────┘
                                 │
                            HTTP REST API
                                 │
┌─────────────────────────────────────────────────────────────────┐
│                      Backend (FastAPI)                         │
├─────────────────────┬─────────────────────┬─────────────────────┤
│    API Routes       │   Execution Engine  │   Plugin System     │
│  - /graphs          │  - Graph executor   │  - Node registry    │
│  - /executions      │  - Node scheduler   │  - Built-in nodes   │
│  - /instances       │  - Context manager  │  - Plugin loader    │
│  - /plugins         │  - Instance exec    │  - External plugins │
└─────────────────────┴─────────────────────┴─────────────────────┘
                                 │
                      Database & Cache Layer
                                 │
┌─────────────────────┬─────────────────────┬─────────────────────┐
│    PostgreSQL       │       Redis         │    File Storage     │
│  - Graph definitions│  - Context cache    │  - Plugin files     │
│  - Execution history│  - Session state    │  - Example graphs   │
│  - Instance state   │  - Smart contexts   │  - Media assets     │
│  - User data        │  - Immutable cache  │                     │
└─────────────────────┴─────────────────────┴─────────────────────┘
```

## Core Components

### 1. Graph Definition System

Graphs are defined using a node-edge structure:

```json
{
  "graph_id": "uuid",
  "name": "Graph Name",
  "nodes": {
    "node_id": {
      "node_type": "input|output|text_transform|...",
      "position": {"x": 100, "y": 200},
      "parameters": {"key": "value"}
    }
  },
  "edges": [
    {
      "source_node": "node1",
      "source_port": "output",
      "target_node": "node2", 
      "target_port": "input"
    }
  ]
}
```

### 2. Execution Engine

The execution engine processes graphs through:

1. **Topological Sorting**: Determines execution order based on dependencies
2. **Node Execution**: Sequential or parallel execution of nodes
3. **Data Flow**: Port-to-port data transmission between nodes
4. **Context Management**: Maintains execution state and intermediate results
5. **Error Handling**: Tracks failures and provides detailed error information

### 3. Node System

Nodes are the fundamental processing units:

- **Base Node Interface**: Abstract class defining execution contract
- **Built-in Nodes**: Core functionality (input, output, text processing, JSON manipulation)
- **AI Nodes**: LLM integration with context management (smart_chat, immutable_chat)
- **Plugin Nodes**: External node types loaded dynamically

### 4. Context Management

Two context systems for AI integration:

- **Smart Context**: Provider-aware context management that adapts to LLM capabilities
- **Immutable Context**: Content-addressable context for efficient memory usage

## Data Flow

1. **Graph Creation**: Frontend creates/edits graphs via REST API
2. **Execution Request**: User triggers execution with input parameters
3. **Graph Processing**: Backend loads graph and plans execution
4. **Node Execution**: Sequential/parallel execution based on dependencies
5. **Result Collection**: Outputs collected and returned to frontend
6. **State Persistence**: Instance state and execution history saved

## Technology Stack

### Backend
- **Framework**: FastAPI (Python 3.11+)
- **Database**: PostgreSQL with SQLAlchemy ORM
- **Cache**: Redis for session and context caching
- **Migration**: Alembic for database schema management
- **Async**: Full async/await support for concurrent execution
- **Validation**: Pydantic for data validation and serialization

### Frontend
- **Framework**: React 18 with TypeScript
- **UI Library**: Tailwind CSS for styling
- **Graph Editor**: ReactFlow for visual node editing
- **State Management**: Zustand for client state
- **API Client**: Axios with React Query for server state
- **Router**: React Router for navigation
- **Build Tool**: Vite for development and build

### Infrastructure
- **Containerization**: Docker & Docker Compose
- **Database**: PostgreSQL 15
- **Cache**: Redis 7
- **Reverse Proxy**: Can be integrated with Nginx/Apache
- **Monitoring**: Structured logging with Python logging

## Key Design Patterns

### 1. Plugin Architecture
- **Node Registry**: Central registry for all node types
- **Plugin Loader**: Dynamic loading of external plugins
- **Manifest System**: YAML-based plugin metadata
- **Dependency Injection**: Loose coupling between components

### 2. Execution Patterns
- **Command Pattern**: Encapsulated execution commands
- **Observer Pattern**: Event-driven execution monitoring
- **Strategy Pattern**: Different execution strategies (sequential, parallel, streaming)
- **Template Method**: Standardized node execution lifecycle

### 3. Data Management
- **Repository Pattern**: Data access abstraction
- **Unit of Work**: Transaction management
- **Content Addressing**: Immutable context storage
- **Event Sourcing**: Execution history tracking

## Security Considerations

- **Input Validation**: Comprehensive validation at API boundaries
- **SQL Injection Prevention**: Parameterized queries via SQLAlchemy
- **Cross-Site Scripting**: Sanitized outputs and CSP headers
- **API Security**: CORS configuration and rate limiting
- **Plugin Sandboxing**: Isolated plugin execution environment

## Scalability Architecture

### Horizontal Scaling
- **Stateless API**: All state in database/cache
- **Load Balancing**: Multiple backend instances
- **Database Scaling**: Read replicas and connection pooling
- **Cache Distribution**: Redis clustering support

### Performance Optimization
- **Async Execution**: Non-blocking I/O operations
- **Context Caching**: Efficient LLM context reuse
- **Parallel Processing**: Concurrent node execution
- **Database Indexing**: Optimized queries for graph operations

## Integration Points

### External Systems
- **LLM Providers**: Anthropic, OpenAI, Ollama, Bedrock
- **Plugin Ecosystem**: External node development
- **API Gateway**: Standard REST API for integration
- **Webhook Support**: Event-driven external notifications

### Development Workflow
- **Version Control**: Git-based graph versioning
- **CI/CD Pipeline**: Automated testing and deployment
- **Documentation**: Auto-generated API documentation
- **Monitoring**: Application metrics and logging

## Future Considerations

- **Distributed Execution**: Cross-node graph execution
- **Real-time Collaboration**: Multi-user graph editing
- **Advanced Scheduling**: Cron-like graph execution
- **ML Pipeline Integration**: Native MLOps workflow support
- **Monitoring Dashboard**: Real-time execution monitoring

This architecture provides a solid foundation for a scalable, maintainable, and extensible graph processing system with strong separation of concerns and clear integration boundaries.