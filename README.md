# Nodecules

A complete node-based graph processing engine for building flexible AI-powered workflows with visual graph editing and conversational interfaces.

## 🚀 Features

### 🎨 **Visual Graph Editor**
- Professional React Flow-based interface with drag-drop node creation
- Real-time node property editing with validation
- Visual connection management with port-specific targeting
- Clean folder-tab style node layout with organized input/output ports

### 💬 **Chat Interface**
- Interactive chat interface for executing graphs conversationally
- Dynamic parameter exposure from graph input nodes
- Automatic conversation context management
- Support for various graph types (interactive, write-only, read-only)

### 🤖 **AI Integration**
- **Ollama** support for local LLM execution
- **Immutable content-addressable contexts** for efficient memory management
- Smart context management with provider-native optimization
- Multiple chat node types (smart_chat, immutable_chat)

### 🔧 **Advanced Graph Management**
- Create, edit, copy, delete, and execute graphs
- Flexible input system (labels, ordinals, node IDs, chat conventions)
- Graph execution by name for API integration
- Real-time parameter configuration and execution

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

### Chat Interface
1. Navigate to the "Chat" tab in the application
2. Select a graph with proper chat conventions:
   - Input node with ID `chat_message` (receives user messages)
   - Output node with ID `chat_response` (displays AI responses)
   - Optional input node `chat_context` (for conversation memory)
3. Additional input nodes appear as user controls (temperature sliders, model selectors, etc.)
4. Type messages and interact with your graph conversationally

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

#### **Core Nodes**
- **Input Node** - Collects user input with configurable labels and data types
- **Output Node** - Displays results with custom labels
- **Text Transform** - Text operations (uppercase, lowercase, title case)
- **Text Filter** - Pattern-based text filtering with regex support
- **Text Concat** - Multi-input text concatenation

#### **AI Nodes**
- **Smart Chat** - Context-aware conversational AI with provider adapters
- **Immutable Chat** - Memory-efficient chat with content-addressable contexts
- Support for parameter inputs (model, temperature, system_prompt)

### Input Resolution Methods

The system supports multiple ways to provide inputs:

1. **By Label** - Use friendly names: `{"greeting": "Hello"}`
2. **By Ordinal** - Use position: `{"input_1": "Hello", "input_2": "World"}`
3. **By Node ID** - Direct node reference: `{"node_abc123": "Hello"}`
4. **Chat Convention** - Special IDs for chat interface: `{"chat_message": "Hello", "chat_context": "..."}`

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

### ✅ **Completed Features**
- **Visual Graph Editor** - Professional React Flow interface
- **Chat Interface** - Conversational graph execution
- **AI Integration** - Ollama support with context management
- **Flexible Input System** - Multiple input resolution methods
- **Content-Addressable Contexts** - Efficient memory management

### 🎯 **Next Phase: Enhanced AI Experience**
- [ ] **Streaming Responses** - Real-time response generation with thinking indicators
- [ ] **Setup Scripts** - Database initialization with example graphs
- [ ] **Additional LLM Providers** - Anthropic Claude, OpenAI, Azure OpenAI
- [ ] **Multi-modal Support** - Images, documents, audio processing

### 🔮 **Future Enhancements**
- [ ] Conditional logic and branching nodes
- [ ] Loop and iteration capabilities  
- [ ] External API connectors
- [ ] File I/O operations
- [ ] Advanced prompt engineering tools

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

**Status**: Production ready with AI-powered graph processing and chat interface  
**Next Milestone**: Streaming responses and enhanced setup tooling
