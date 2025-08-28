# Getting Started with Nodecules

## Quick Start

### Prerequisites
- Docker and Docker Compose
- Python 3.11+ (for development)
- Node.js 18+ (for frontend development)

### 1. Start the Development Environment

```bash
# Start all services
docker-compose up -d

# Check service status
docker-compose ps
```

### 2. Access the Application

- **Frontend**: http://localhost:3000
- **Backend API**: http://localhost:8000
- **API Docs**: http://localhost:8000/docs

### 3. Create Your First Graph

1. Open http://localhost:3000 in your browser
2. Click "New Graph" 
3. Use the node palette to drag nodes onto the canvas
4. Connect nodes by dragging from output ports to input ports
5. Set node parameters in the properties panel
6. Click "Execute" to run your graph

## Available Node Types

### Built-in Nodes
- **Input**: Provide data to your graph
- **Text Transform**: Transform text (uppercase, lowercase, etc.)
- **Text Filter**: Filter text using regex patterns
- **Text Concat**: Concatenate multiple text inputs
- **Output**: Display results

### Example Plugin Nodes
- **Example Processor**: Demonstrates custom node development

## Example Workflows

### Simple Text Processing
1. Add an **Input** node with some text
2. Add a **Text Transform** node set to "uppercase"
3. Add an **Output** node
4. Connect: Input → Text Transform → Output
5. Execute to see the result

### Text Analysis Pipeline
1. **Input** → "Hello world, this is a test"
2. **Text Filter** → Extract words with regex pattern `\\w+`
3. **Text Transform** → Convert to uppercase
4. **Output** → Display results

## Development

### Backend Development
```bash
cd backend
poetry install
poetry run uvicorn nodecules.main:app --reload --host 0.0.0.0 --port 8000
```

### Frontend Development  
```bash
cd frontend
npm install
npm run dev
```

### Database Migrations
```bash
cd backend
poetry run alembic revision --autogenerate -m "Description"
poetry run alembic upgrade head
```

## Creating Custom Plugins

1. Create a new directory in `plugins/`
2. Add a `plugin.yaml` manifest file
3. Create your node classes inheriting from `BaseNode`
4. Restart the backend to load your plugin

See `plugins/example_plugin/` for a complete example.

## Architecture Overview

- **Backend**: FastAPI with PostgreSQL and Redis
- **Frontend**: React with React Flow for visual editing  
- **Execution Engine**: Topological sort with async processing
- **Plugin System**: Dynamic loading with YAML manifests
- **Data Storage**: PostgreSQL for graphs, Redis for caching

## Next Steps

1. Explore the example workflows
2. Try creating custom nodes
3. Build more complex processing pipelines
4. Add multi-modal data support

For detailed documentation, see the `planning/` directory.