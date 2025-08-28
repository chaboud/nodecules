"""Main FastAPI application."""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.graphs import router as graphs_router
from .api.executions import router as executions_router
from .api.plugins import router as plugins_router
from .core.executor import NodeRegistry
from .plugins.loader import PluginManager
from .plugins.builtin_nodes import BUILTIN_NODES

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Global plugin manager and node registry
plugin_manager: PluginManager = None
node_registry: NodeRegistry = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    global plugin_manager, node_registry
    
    # Startup
    logger.info("Starting up nodecules...")
    
    # Initialize plugin system
    plugin_manager = PluginManager(["./plugins"])
    plugin_manager.initialize()
    
    # Initialize node registry with built-in nodes
    node_registry = NodeRegistry()
    for node_type, node_class in BUILTIN_NODES.items():
        node_registry.register(node_type, node_class)
        
    # Add plugin nodes to registry
    plugin_nodes = plugin_manager.get_node_registry()
    for node_type, node_class in plugin_nodes.items():
        node_registry.register(node_type, node_class)
        
    logger.info(f"Loaded {len(node_registry.list_types())} node types")
    
    # Store in app state for access in routes
    app.state.plugin_manager = plugin_manager
    app.state.node_registry = node_registry
    
    yield
    
    # Shutdown
    logger.info("Shutting down nodecules...")


# Create FastAPI app
app = FastAPI(
    title="Nodecules API",
    description="A Python node-based graph processing engine",
    version="0.1.0",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure properly for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(graphs_router, prefix="/api/v1/graphs", tags=["graphs"])
app.include_router(executions_router, prefix="/api/v1/executions", tags=["executions"])
app.include_router(plugins_router, prefix="/api/v1/plugins", tags=["plugins"])


@app.get("/")
async def root():
    """Root endpoint."""
    return {"message": "Welcome to Nodecules API", "version": "0.1.0"}


@app.get("/api/v1/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "message": "Service is running"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)