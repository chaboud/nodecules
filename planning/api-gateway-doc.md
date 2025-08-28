# API Gateway & Service Layer

## Overview

The API Gateway provides a unified interface for all client interactions with the pipeline system. It handles authentication, rate limiting, request routing, and protocol translation between REST, GraphQL, and WebSocket connections.

## Architecture

### Gateway Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                         Clients                              │
│   Web UI | Mobile | CLI | Third-party Apps | Notebooks      │
└──────────────────────────────────────────────────────────────┘
                              │
                    ┌─────────▼─────────┐
                    │   Load Balancer    │
                    │    (AWS ALB/NLB)   │
                    └─────────┬─────────┘
                              │
        ┌─────────────────────┴─────────────────────┐
        │                                           │
┌───────▼────────┐  ┌──────────▼────────┐  ┌──────▼────────┐
│   REST API     │  │   GraphQL API     │  │  WebSocket    │
│   (FastAPI)    │  │   (Strawberry)    │  │  (Socket.io)  │
└───────┬────────┘  └──────────┬────────┘  └──────┬────────┘
        │                      │                   │
        └──────────────────────┴───────────────────┘
                              │
                    ┌─────────▼─────────┐
                    │  Service Router   │
                    │   & Middleware    │
                    └─────────┬─────────┘
                              │
    ┌──────────┬──────────────┴──────────────┬──────────┐
    │          │                             │          │
┌───▼──┐  ┌───▼──┐  ┌────▼────┐  ┌────▼────┐  ┌────▼────┐
│ Auth │  │ Data │  │Execution│  │Annotation│  │ Events  │
│ Svc  │  │ Svc  │  │   Svc   │  │   Svc    │  │   Svc   │
└──────┘  └──────┘  └─────────┘  └──────────┘  └─────────┘
```

## REST API Implementation

### FastAPI Setup

```python
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from typing import Optional, List, Dict, Any
import uvicorn

# Create FastAPI app
app = FastAPI(
    title="Pipeline API",
    description="Node-based information pipeline system",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc"
)

# Add middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Custom middleware
@app.middleware("http")
async def add_request_id(request: Request, call_next):
    """Add request ID for tracing"""
    import uuid
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response
```

### API Routes

```python
from pydantic import BaseModel, Field
from datetime import datetime

# Request/Response models
class DataIngestionRequest(BaseModel):
    """Request model for data ingestion"""
    content: str = Field(..., description="Content to ingest")
    content_type: str = Field(..., description="MIME type of content")
    metadata: Dict[str, Any] = Field(default={}, description="Additional metadata")
    annotations: Optional[List[str]] = Field(None, description="Initial annotations")
    
class DataIngestionResponse(BaseModel):
    """Response model for data ingestion"""
    object_id: str
    status: str
    created_at: datetime
    processing_time_ms: float
    
class GraphExecutionRequest(BaseModel):
    """Request model for graph execution"""
    graph: Dict[str, Any] = Field(..., description="Graph definition")
    inputs: Dict[str, Any] = Field(default={}, description="Input data")
    options: Dict[str, Any] = Field(default={}, description="Execution options")
    
# API endpoints
@app.post("/api/v1/ingest", response_model=DataIngestionResponse)
async def ingest_data(
    request: DataIngestionRequest,
    user: User = Depends(get_current_user),
    services: ServiceContainer = Depends(get_services)
):
    """Ingest data into the pipeline"""
    start_time = time.time()
    
    # Process ingestion
    object_id = await services.data_service.ingest(
        content=request.content,
        content_type=request.content_type,
        metadata={**request.metadata, "user_id": user.id},
        annotations=request.annotations
    )
    
    return DataIngestionResponse(
        object_id=object_id,
        status="success",
        created_at=datetime.utcnow(),
        processing_time_ms=(time.time() - start_time) * 1000
    )

@app.post("/api/v1/execute")
async def execute_graph(
    request: GraphExecutionRequest,
    user: User = Depends(get_current_user),
    services: ServiceContainer = Depends(get_services)
):
    """Execute a processing graph"""
    
    # Validate graph
    validation = services.execution_service.validate_graph(request.graph)
    if not validation.is_valid:
        raise HTTPException(400, detail=validation.errors)
        
    # Submit for execution
    execution_id = await services.execution_service.submit(
        graph=request.graph,
        inputs=request.inputs,
        options=request.options,
        user_id=user.id
    )
    
    return {
        "execution_id": execution_id,
        "status": "submitted",
        "estimated_time_seconds": validation.estimated_time
    }

@app.get("/api/v1/search")
async def search_data(
    query: str,
    modality: str = "text",
    limit: int = 10,
    offset: int = 0,
    filters: Optional[str] = None,
    user: User = Depends(get_current_user),
    services: ServiceContainer = Depends(get_services)
):
    """Search across ingested data"""
    
    # Parse filters
    filter_dict = json.loads(filters) if filters else {}
    
    # Perform search
    results = await services.data_service.search(
        query=query,
        modality=modality,
        limit=limit,
        offset=offset,
        filters=filter_dict,
        user_id=user.id
    )
    
    return {
        "results": results,
        "total": len(results),
        "query": query,
        "filters": filter_dict
    }
```

### File Upload Handling

```python
from fastapi import File, UploadFile, Form
from typing import List
import aiofiles

@app.post("/api/v1/upload")
async def upload_files(
    files: List[UploadFile] = File(...),
    metadata: str = Form("{}"),
    user: User = Depends(get_current_user),
    services: ServiceContainer = Depends(get_services)
):
    """Handle multi-file uploads"""
    
    results = []
    metadata_dict = json.loads(metadata)
    
    for file in files:
        # Save file temporarily
        temp_path = f"/tmp/{file.filename}"
        async with aiofiles.open(temp_path, 'wb') as f:
            content = await file.read()
            await f.write(content)
            
        # Determine file type
        content_type = file.content_type or "application/octet-stream"
        
        # Ingest file
        object_id = await services.data_service.ingest_file(
            file_path=temp_path,
            content_type=content_type,
            metadata={
                **metadata_dict,
                "filename": file.filename,
                "user_id": user.id
            }
        )
        
        results.append({
            "filename": file.filename,
            "object_id": object_id,
            "size": len(content)
        })
        
        # Clean up temp file
        os.remove(temp_path)
        
    return {"uploaded": results, "count": len(results)}
```

## GraphQL API

### Schema Definition

```python
import strawberry
from strawberry.fastapi import GraphQLRouter
from typing import Optional, List
from datetime import datetime

@strawberry.type
class DataObject:
    """GraphQL type for data objects"""
    object_id: str
    object_type: str
    content_hash: str
    storage_location: str
    metadata: strawberry.scalars.JSON
    created_at: datetime
    annotations: List['Annotation']
    
@strawberry.type
class Annotation:
    """GraphQL type for annotations"""
    annotation_id: str
    annotator_id: str
    annotation_type: str
    content: strawberry.scalars.JSON
    confidence: float
    created_at: datetime
    
@strawberry.type
class ExecutionStatus:
    """GraphQL type for execution status"""
    execution_id: str
    status: str
    progress: float
    current_node: Optional[str]
    errors: List[str]
    started_at: datetime
    completed_at: Optional[datetime]

@strawberry.type
class Query:
    """GraphQL query definitions"""
    
    @strawberry.field
    async def data_object(self, object_id: str) -> Optional[DataObject]:
        """Get single data object by ID"""
        return await get_data_object(object_id)
        
    @strawberry.field
    async def search_objects(
        self,
        query: str,
        modality: str = "text",
        limit: int = 10
    ) -> List[DataObject]:
        """Search for data objects"""
        return await search_data_objects(query, modality, limit)
        
    @strawberry.field
    async def execution_status(
        self,
        execution_id: str
    ) -> Optional[ExecutionStatus]:
        """Get execution status"""
        return await get_execution_status(execution_id)
        
    @strawberry.field
    async def available_nodes(
        self,
        category: Optional[str] = None
    ) -> List[strawberry.scalars.JSON]:
        """Get available node types"""
        return await get_available_nodes(category)

@strawberry.type
class Mutation:
    """GraphQL mutation definitions"""
    
    @strawberry.mutation
    async def ingest_data(
        self,
        content: str,
        content_type: str,
        metadata: strawberry.scalars.JSON = {}
    ) -> DataObject:
        """Ingest new data"""
        object_id = await ingest_data_impl(content, content_type, metadata)
        return await get_data_object(object_id)
        
    @strawberry.mutation
    async def execute_graph(
        self,
        graph: strawberry.scalars.JSON,
        inputs: strawberry.scalars.JSON = {}
    ) -> ExecutionStatus:
        """Execute processing graph"""
        execution_id = await execute_graph_impl(graph, inputs)
        return await get_execution_status(execution_id)
        
    @strawberry.mutation
    async def add_annotation(
        self,
        object_id: str,
        annotation_type: str,
        content: strawberry.scalars.JSON
    ) -> Annotation:
        """Add annotation to object"""
        return await add_annotation_impl(object_id, annotation_type, content)

@strawberry.type
class Subscription:
    """GraphQL subscription definitions"""
    
    @strawberry.subscription
    async def execution_updates(
        self,
        execution_id: str
    ) -> AsyncGenerator[ExecutionStatus, None]:
        """Subscribe to execution status updates"""
        async for update in watch_execution(execution_id):
            yield update
            
    @strawberry.subscription
    async def data_changes(
        self,
        filters: Optional[strawberry.scalars.JSON] = None
    ) -> AsyncGenerator[DataObject, None]:
        """Subscribe to data changes"""
        async for change in watch_data_changes(filters):
            yield change

# Create GraphQL app
schema = strawberry.Schema(
    query=Query,
    mutation=Mutation,
    subscription=Subscription
)

graphql_app = GraphQLRouter(
    schema,
    path="/api/graphql",
    graphql_ide="apollo-sandbox"
)

# Add to FastAPI
app.include_router(graphql_app, prefix="")

## WebSocket Implementation

### Real-time Communication

```python
from fastapi import WebSocket, WebSocketDisconnect
from typing import Dict, Set
import json

class ConnectionManager:
    """Manage WebSocket connections"""
    
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.subscriptions: Dict[str, Set[str]] = {}
        
    async def connect(self, websocket: WebSocket, client_id: str):
        """Accept new WebSocket connection"""
        await websocket.accept()
        self.active_connections[client_id] = websocket
        
    def disconnect(self, client_id: str):
        """Handle disconnection"""
        if client_id in self.active_connections:
            del self.active_connections[client_id]
            
        # Clean up subscriptions
        for topic in list(self.subscriptions.keys()):
            if client_id in self.subscriptions[topic]:
                self.subscriptions[topic].remove(client_id)
                if not self.subscriptions[topic]:
                    del self.subscriptions[topic]
                    
    async def send_personal_message(self, message: str, client_id: str):
        """Send message to specific client"""
        if client_id in self.active_connections:
            websocket = self.active_connections[client_id]
            await websocket.send_text(message)
            
    async def broadcast(self, message: str, topic: str = None):
        """Broadcast message to all or topic subscribers"""
        if topic and topic in self.subscriptions:
            # Send to topic subscribers
            for client_id in self.subscriptions[topic]:
                if client_id in self.active_connections:
                    await self.active_connections[client_id].send_text(message)
        else:
            # Send to all connected clients
            for websocket in self.active_connections.values():
                await websocket.send_text(message)
                
    def subscribe(self, client_id: str, topic: str):
        """Subscribe client to topic"""
        if topic not in self.subscriptions:
            self.subscriptions[topic] = set()
        self.subscriptions[topic].add(client_id)
        
    def unsubscribe(self, client_id: str, topic: str):
        """Unsubscribe client from topic"""
        if topic in self.subscriptions and client_id in self.subscriptions[topic]:
            self.subscriptions[topic].remove(client_id)

# Connection manager instance
manager = ConnectionManager()

@app.websocket("/ws/{client_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    client_id: str,
    services: ServiceContainer = Depends(get_services)
):
    """WebSocket endpoint for real-time communication"""
    
    await manager.connect(websocket, client_id)
    
    try:
        while True:
            # Receive message from client
            data = await websocket.receive_text()
            message = json.loads(data)
            
            # Handle different message types
            if message["type"] == "subscribe":
                manager.subscribe(client_id, message["topic"])
                await manager.send_personal_message(
                    json.dumps({
                        "type": "subscribed",
                        "topic": message["topic"]
                    }),
                    client_id
                )
                
            elif message["type"] == "unsubscribe":
                manager.unsubscribe(client_id, message["topic"])
                
            elif message["type"] == "execution_status":
                # Get execution status and send back
                status = await services.execution_service.get_status(
                    message["execution_id"]
                )
                await manager.send_personal_message(
                    json.dumps({
                        "type": "execution_status",
                        "data": status
                    }),
                    client_id
                )
                
            elif message["type"] == "chat_message":
                # Process chat message
                response = await services.chat_service.process_message(
                    message["content"],
                    client_id
                )
                await manager.send_personal_message(
                    json.dumps({
                        "type": "chat_response",
                        "data": response
                    }),
                    client_id
                )
                
    except WebSocketDisconnect:
        manager.disconnect(client_id)
```

### Event Broadcasting

```python
class EventBroadcaster:
    """Broadcast events to WebSocket clients"""
    
    def __init__(self, manager: ConnectionManager):
        self.manager = manager
        
    async def broadcast_execution_update(
        self,
        execution_id: str,
        status: Dict
    ):
        """Broadcast execution status update"""
        message = json.dumps({
            "type": "execution_update",
            "execution_id": execution_id,
            "data": status
        })
        await self.manager.broadcast(message, f"execution:{execution_id}")
        
    async def broadcast_data_change(
        self,
        object_id: str,
        change_type: str,
        data: Dict
    ):
        """Broadcast data change event"""
        message = json.dumps({
            "type": "data_change",
            "object_id": object_id,
            "change_type": change_type,
            "data": data
        })
        await self.manager.broadcast(message, "data_changes")
        
    async def broadcast_notification(
        self,
        user_id: str,
        notification: Dict
    ):
        """Send notification to specific user"""
        message = json.dumps({
            "type": "notification",
            "data": notification
        })
        await self.manager.send_personal_message(message, user_id)
```

## Authentication & Authorization

### JWT Authentication

```python
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from passlib.context import CryptContext
from datetime import datetime, timedelta

# Configuration
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Bearer token dependency
security = HTTPBearer()

class AuthService:
    """Authentication service"""
    
    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """Verify password against hash"""
        return pwd_context.verify(plain_password, hashed_password)
        
    def get_password_hash(self, password: str) -> str:
        """Hash password"""
        return pwd_context.hash(password)
        
    def create_access_token(self, data: dict, expires_delta: timedelta = None):
        """Create JWT access token"""
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(minutes=15)
            
        to_encode.update({"exp": expire})
        encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
        return encoded_jwt
        
    def verify_token(self, token: str) -> Dict:
        """Verify JWT token"""
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            return payload
        except JWTError:
            raise HTTPException(
                status_code=401,
                detail="Could not validate credentials"
            )

# Dependency injection
async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    auth_service: AuthService = Depends(get_auth_service)
) -> User:
    """Get current authenticated user"""
    
    token = credentials.credentials
    payload = auth_service.verify_token(token)
    
    user = await get_user_by_id(payload.get("sub"))
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Could not validate credentials"
        )
        
    return user

# Login endpoint
@app.post("/api/v1/auth/login")
async def login(
    username: str,
    password: str,
    auth_service: AuthService = Depends(get_auth_service)
):
    """User login"""
    
    user = await get_user_by_username(username)
    if not user or not auth_service.verify_password(password, user.hashed_password):
        raise HTTPException(
            status_code=401,
            detail="Incorrect username or password"
        )
        
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = auth_service.create_access_token(
        data={"sub": user.id},
        expires_delta=access_token_expires
    )
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60
    }
```

### Role-Based Access Control

```python
from enum import Enum
from typing import List

class Role(str, Enum):
    """User roles"""
    ADMIN = "admin"
    USER = "user"
    VIEWER = "viewer"
    
class Permission(str, Enum):
    """System permissions"""
    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    EXECUTE = "execute"
    ADMIN = "admin"

class RBACService:
    """Role-based access control service"""
    
    def __init__(self):
        self.role_permissions = {
            Role.ADMIN: [Permission.READ, Permission.WRITE, Permission.DELETE, Permission.EXECUTE, Permission.ADMIN],
            Role.USER: [Permission.READ, Permission.WRITE, Permission.EXECUTE],
            Role.VIEWER: [Permission.READ]
        }
        
    def has_permission(
        self,
        user: User,
        permission: Permission,
        resource: Optional[str] = None
    ) -> bool:
        """Check if user has permission"""
        
        # Check role-based permissions
        user_permissions = self.role_permissions.get(user.role, [])
        if permission not in user_permissions:
            return False
            
        # Check resource-specific permissions if applicable
        if resource:
            return self.check_resource_permission(user, resource, permission)
            
        return True
        
    def check_resource_permission(
        self,
        user: User,
        resource: str,
        permission: Permission
    ) -> bool:
        """Check resource-specific permissions"""
        
        # Implementation depends on resource type
        # Could check ownership, sharing settings, etc.
        return True

# Permission dependency
def require_permission(permission: Permission):
    """Dependency to require specific permission"""
    
    async def permission_checker(
        user: User = Depends(get_current_user),
        rbac: RBACService = Depends(get_rbac_service)
    ):
        if not rbac.has_permission(user, permission):
            raise HTTPException(
                status_code=403,
                detail="Insufficient permissions"
            )
        return user
        
    return permission_checker

# Protected endpoint example
@app.delete("/api/v1/data/{object_id}")
async def delete_data(
    object_id: str,
    user: User = Depends(require_permission(Permission.DELETE)),
    services: ServiceContainer = Depends(get_services)
):
    """Delete data object (requires DELETE permission)"""
    
    await services.data_service.delete(object_id, user.id)
    return {"status": "deleted", "object_id": object_id}
```

## Rate Limiting

### Implementation

```python
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# Create limiter
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["1000 per hour"]
)

# Add error handler
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Apply rate limits
@app.post("/api/v1/ingest")
@limiter.limit("100 per minute")
async def ingest_data_limited(request: Request, data: DataIngestionRequest):
    """Rate-limited ingestion endpoint"""
    return await ingest_data(data)

# Custom rate limiting
class UserRateLimiter:
    """User-specific rate limiting"""
    
    def __init__(self, redis_client):
        self.redis = redis_client
        self.limits = {
            Role.ADMIN: {"requests": 10000, "window": 3600},
            Role.USER: {"requests": 1000, "window": 3600},
            Role.VIEWER: {"requests": 100, "window": 3600}
        }
        
    async def check_rate_limit(self, user: User) -> bool:
        """Check if user has exceeded rate limit"""
        
        key = f"rate_limit:{user.id}"
        limit = self.limits[user.role]
        
        # Get current count
        count = await self.redis.incr(key)
        
        # Set expiry on first request
        if count == 1:
            await self.redis.expire(key, limit["window"])
            
        return count <= limit["requests"]
        
    async def get_remaining(self, user: User) -> Dict:
        """Get remaining requests for user"""
        
        key = f"rate_limit:{user.id}"
        limit = self.limits[user.role]
        
        count = await self.redis.get(key) or 0
        ttl = await self.redis.ttl(key)
        
        return {
            "limit": limit["requests"],
            "remaining": max(0, limit["requests"] - int(count)),
            "reset_in": ttl
        }
```

## Service Layer

### Service Container

```python
class ServiceContainer:
    """Container for all services"""
    
    def __init__(self, config: Dict):
        self.config = config
        self._services = {}
        
    def register(self, name: str, service: Any):
        """Register service"""
        self._services[name] = service
        
    def get(self, name: str) -> Any:
        """Get service by name"""
        if name not in self._services:
            raise KeyError(f"Service {name} not found")
        return self._services[name]
        
    @property
    def data_service(self) -> DataService:
        return self.get("data")
        
    @property
    def execution_service(self) -> ExecutionService:
        return self.get("execution")
        
    @property
    def annotation_service(self) -> AnnotationService:
        return self.get("annotation")
        
    @property
    def event_service(self) -> EventService:
        return self.get("event")

# Service initialization
def initialize_services(config: Dict) -> ServiceContainer:
    """Initialize all services"""
    
    container = ServiceContainer(config)
    
    # Initialize data layer
    db_manager = DatabaseManager(config["database"]["connection_string"])
    storage_manager = ObjectStorageManager(**config["storage"])
    cache_manager = CacheManager(**config["cache"])
    
    # Initialize services
    container.register("data", DataService(db_manager, storage_manager, cache_manager))
    container.register("execution", ExecutionService(db_manager, cache_manager))
    container.register("annotation", AnnotationService(db_manager))
    container.register("event", EventService())
    
    return container

# Dependency injection
services_container = None

def get_services() -> ServiceContainer:
    """Get services container"""
    global services_container
    if services_container is None:
        config = load_config()
        services_container = initialize_services(config)
    return services_container
```

### Service Interfaces

```python
from abc import ABC, abstractmethod

class BaseService(ABC):
    """Base service interface"""
    
    @abstractmethod
    async def health_check(self) -> Dict:
        """Check service health"""
        pass

class DataService(BaseService):
    """Data management service"""
    
    def __init__(self, db: DatabaseManager, storage: ObjectStorageManager, cache: CacheManager):
        self.db = db
        self.storage = storage
        self.cache = cache
        
    async def ingest(self, content: str, content_type: str, metadata: Dict, annotations: List[str] = None) -> str:
        """Ingest data into pipeline"""
        # Implementation
        pass
        
    async def search(self, query: str, modality: str, limit: int, offset: int, filters: Dict) -> List[Dict]:
        """Search data"""
        # Implementation
        pass
        
    async def get(self, object_id: str) -> Optional[DataObject]:
        """Get data object by ID"""
        # Implementation
        pass
        
    async def delete(self, object_id: str, user_id: str) -> bool:
        """Delete data object"""
        # Implementation
        pass
        
    async def health_check(self) -> Dict:
        """Check service health"""
        return {
            "status": "healthy",
            "database": await self.db.ping(),
            "storage": await self.storage.ping(),
            "cache": await self.cache.ping()
        }
```

## Error Handling

### Global Error Handler

```python
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request, exc):
    """Handle HTTP exceptions"""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.status_code,
                "message": exc.detail,
                "request_id": getattr(request.state, "request_id", None)
            }
        }
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    """Handle validation errors"""
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": 422,
                "message": "Validation error",
                "details": exc.errors(),
                "request_id": getattr(request.state, "request_id", None)
            }
        }
    )

@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    """Handle general exceptions"""
    import traceback
    
    # Log error
    logger.error(f"Unhandled exception: {exc}\n{traceback.format_exc()}")
    
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": 500,
                "message": "Internal server error",
                "request_id": getattr(request.state, "request_id", None)
            }
        }
    )
```

## API Documentation

### OpenAPI Extensions

```python
from fastapi.openapi.utils import get_openapi

def custom_openapi():
    """Customize OpenAPI schema"""
    
    if app.openapi_schema:
        return app.openapi_schema
        
    openapi_schema = get_openapi(
        title="Pipeline API",
        version="1.0.0",
        description="Node-based information pipeline system API",
        routes=app.routes,
    )
    
    # Add security schemes
    openapi_schema["components"]["securitySchemes"] = {
        "Bearer": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }
    }
    
    # Add tags
    openapi_schema["tags"] = [
        {"name": "data", "description": "Data management operations"},
        {"name": "execution", "description": "Graph execution operations"},
        {"name": "search", "description": "Search operations"},
        {"name": "auth", "description": "Authentication operations"}
    ]
    
    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi
```

## Configuration

```yaml
# api_gateway.yaml
server:
  host: 0.0.0.0
  port: 8000
  workers: 4
  reload: false
  
cors:
  origins:
    - http://localhost:3000
    - https://app.example.com
  credentials: true
  methods: ["GET", "POST", "PUT", "DELETE"]
  headers: ["*"]
  
authentication:
  secret_key: ${SECRET_KEY}
  algorithm: HS256
  access_token_expire_minutes: 30
  refresh_token_expire_days: 7
  
rate_limiting:
  default_limit: "1000 per hour"
  burst_limit: "100 per minute"
  redis_url: redis://localhost:6379/1
  
websocket:
  ping_interval: 30
  ping_timeout: 10
  max_connections: 1000
  max_message_size: 1048576  # 1MB
  
monitoring:
  metrics_enabled: true
  tracing_enabled: true
  health_check_path: /health
  metrics_path: /metrics
```

## Next Steps

1. Review [Frontend Architecture](frontend-architecture-doc)
2. Implement [WebSocket Client Guide](websocket-client-guide)
3. Configure [API Security Best Practices](api-security-guide)
4. Set up [API Testing Framework](api-testing-guide)
