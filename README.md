# Nodecules

A Python node-based graph processing engine for building flexible, scalable data pipelines.

## Features

- Visual graph editor for creating processing workflows
- Extensible plugin system for custom node types
- Multi-modal data processing (text, images, audio, video)
- Real-time execution with progress tracking
- RESTful API and WebSocket support
- Docker-based development environment

## Quick Start

### Development Setup

1. Install dependencies:
```bash
poetry install
```

2. Start the development environment:
```bash
docker-compose up -d
```

3. Run database migrations:
```bash
poetry run alembic upgrade head
```

4. Start the backend server:
```bash
poetry run uvicorn nodecules.main:app --reload --host 0.0.0.0 --port 8000
```

5. Start the frontend development server:
```bash
cd frontend
npm install
npm run dev
```

## Architecture

- **Backend**: FastAPI with SQLAlchemy and PostgreSQL
- **Frontend**: React with TypeScript and React Flow
- **Execution Engine**: Topological sort with async processing
- **Data Storage**: PostgreSQL + Redis + MinIO
- **Plugin System**: Dynamic loading with sandboxing

## Project Structure

```
nodecules/
├── backend/
│   ├── nodecules/           # Main Python package
│   │   ├── core/            # Core execution engine
│   │   ├── plugins/         # Plugin system
│   │   ├── api/             # FastAPI routes
│   │   ├── models/          # SQLAlchemy models
│   │   └── services/        # Business logic
│   ├── alembic/             # Database migrations
│   └── tests/
├── frontend/
│   ├── src/
│   │   ├── components/      # React components
│   │   ├── features/        # Feature modules
│   │   ├── services/        # API clients
│   │   └── stores/          # State management
│   └── public/
├── plugins/                 # Plugin directory
└── docker/                  # Docker configurations
```

## License

MIT