# Nodecules

A complete node-based graph processing engine for building flexible, scalable data processing workflows. Designed for general-purpose data processing with extensible LLM integration capabilities.

## 🚀 Features

- **Visual Graph Editor** - Professional React Flow-based interface with drag-drop
- **Complete Graph Management** - Create, edit, copy, delete, and execute graphs
- **Flexible Input System** - Multiple input resolution methods (labels, ordinals, node IDs)
- **Graph Execution by Name** - Call graphs by friendly names instead of UUIDs
- **Plugin System** - Extensible architecture for custom node types
- **REST API** - Full API access for external integrations
- **Real-time Execution** - Live execution with labeled outputs
- **Production Ready** - Docker-based environment with PostgreSQL + Redis

## 🏁 Quick Start

### Prerequisites
- Docker and Docker Compose
- Node.js 18+ (for frontend)
- Python 3.9+ with Poetry (for backend)

### Installation

1. **Clone the repository**:
   ```bash
   git clone git@github.com:chaboud/nodecules.git
   cd nodecules
   ```

2. **Start the database services**:
   ```bash
   docker-compose up -d
   ```

3. **Set up the backend**:
   ```bash
   ./dev-setup.sh  # Creates venv and installs dependencies
   cd backend
   poetry shell
   uvicorn nodecules.main:app --reload --host 0.0.0.0
   ```

4. **Start the frontend** (in a new terminal):
   ```bash
   cd frontend
   npm install
   npm run dev
   ```

5. **Access the application**:
   - Frontend: http://localhost:3000
   - Backend API: http://localhost:8000
   - API Docs: http://localhost:8000/docs

## 🎯 Usage

### Visual Graph Editor
1. Navigate to http://localhost:3000
2. Click "New Graph" to create a workflow
3. Drag nodes from the palette to build your graph
4. Configure node parameters in the properties panel
5. Execute graphs and view results in real-time

### API Integration

#### Execute a graph by name:
```bash
# Basic execution
curl -s -X POST http://localhost:8000/api/v1/executions/ \
  -H "Content-Type: application/json" \
  -d '{"graph_id": "My Workflow"}' | \
  jq -r '.outputs | to_entries[] | select(.value.label == "Output") | .value.result'

# With custom inputs
curl -s -X POST http://localhost:8000/api/v1/executions/ \
  -H "Content-Type: application/json" \
  -d '{
    "graph_id": "Text Processing",
    "inputs": {
      "input_1": "Hello World",
      "greeting": "Custom input by label"
    }
  }' | jq '.outputs'
```

#### Get graph schema:
```bash
curl -s "http://localhost:8000/api/v1/graphs/My%20Workflow/schema" | jq '.'
```

#### List available graphs:
```bash
curl -s "http://localhost:8000/api/v1/graphs/" | jq '.[].name'
```

### Built-in Node Types

- **Input Node** - Collects user input with configurable labels and data types
- **Text Transform** - Transforms text (uppercase, lowercase, title case)
- **Text Filter** - Filters text based on patterns and conditions
- **Text Concat** - Concatenates multiple text inputs
- **Output Node** - Displays results with custom labels

### Input Resolution Methods

The system supports multiple ways to provide inputs:

1. **By Label** - Use friendly names: `{"greeting": "Hello"}`
2. **By Ordinal** - Use position: `{"input_1": "Hello", "input_2": "World"}`
3. **By Node ID** - Direct node reference: `{"node_abc123": "Hello"}`

## 🔧 Development

### Project Structure
```
nodecules/
├── backend/
│   ├── nodecules/           # Main Python package
│   │   ├── core/            # Execution engine (topological sort)
│   │   ├── plugins/         # Built-in and custom nodes
│   │   ├── api/             # FastAPI routes
│   │   └── models/          # SQLAlchemy schemas
│   └── tests/
├── frontend/
│   ├── src/
│   │   ├── features/graph/  # Graph editor components
│   │   ├── services/        # API client
│   │   └── stores/          # Zustand state management
│   └── public/
├── plugins/                 # External plugin directory
└── docker-compose.yml       # Development environment
```

### Adding Custom Nodes

1. Create a plugin directory in `plugins/`
2. Implement node class extending `BaseNode`
3. Define node specification with inputs/outputs/parameters
4. Register in `plugin.yaml`

Example node:
```python
class MyCustomNode(BaseNode):
    NODE_TYPE = "my_custom"
    
    def __init__(self):
        spec = NodeSpec(
            node_type=self.NODE_TYPE,
            display_name="My Custom Node",
            description="Does custom processing",
            inputs=[PortSpec(name="input", data_type=DataType.TEXT)],
            outputs=[PortSpec(name="output", data_type=DataType.TEXT)]
        )
        super().__init__(spec)
    
    async def execute(self, context: ExecutionContext, node_data: NodeData) -> Dict[str, Any]:
        input_value = context.get_input_value(node_data.node_id, "input")
        result = self.process(input_value)
        return {"output": result}
```

## 🎭 Examples

### Sample Workflow: "Potato Farmer!"
This example graph demonstrates text processing:

```bash
# Execute with custom input
curl -s -X POST http://localhost:8000/api/v1/executions/ \
  -H "Content-Type: application/json" \
  -d '{
    "graph_id": "Potato farmer!",
    "inputs": {
      "input_1": "Elite potato farmers from Idaho",
      "input_2": "UNITE FOR BETTER SPUDS"
    }
  }' | jq -r '.outputs | to_entries[] | select(.value.label == "Output") | .value.result'
```

Output: `ELITE POTATO FARMERS FROM IDAHO UNITE FOR BETTER SPUDS !!!`

## 🛠️ Architecture

- **Backend**: FastAPI + SQLAlchemy + PostgreSQL
- **Frontend**: React 18 + TypeScript + React Flow + Tailwind CSS
- **State Management**: Zustand
- **Execution Engine**: Topological sort with async processing (Kahn's algorithm)
- **Data Storage**: PostgreSQL (graphs/executions) + Redis (caching)
- **Development**: Docker Compose + Poetry + Vite

## 🚧 Roadmap

### Current Status: ✅ Fully Functional System

### Next Phase: LLM Integration
- [ ] **Process with LLM node** supporting:
  - [ ] Ollama (local models)
  - [ ] Anthropic Claude API
  - [ ] AWS Bedrock
  - [ ] OpenAI API
  - [ ] Azure OpenAI
- [ ] **Advanced prompt engineering nodes**
- [ ] **Multi-modal support** (images, documents, audio)

### Future Enhancements
- [ ] Conditional logic nodes
- [ ] Loop and iteration capabilities
- [ ] External API connectors
- [ ] File I/O operations
- [ ] Real-time streaming execution

## 📄 API Documentation

Complete API documentation is available at http://localhost:8000/docs when running the development server.

Key endpoints:
- `POST /api/v1/executions/` - Execute graphs
- `GET/POST /api/v1/graphs/` - Manage graphs
- `GET /api/v1/graphs/{name}/schema` - Get graph schema
- `GET /api/v1/plugins/nodes` - List available node types

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests
5. Submit a pull request

## 📝 License

MIT License - see [LICENSE](LICENSE) file for details.

---

**Status**: Production ready with full graph processing capabilities  
**Next Milestone**: LLM integration for advanced AI workflows
