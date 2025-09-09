"""Context storage and retrieval nodes for stateless AI providers."""

import hashlib
import uuid
import random
import string
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from ..core.types import NodeSpec, PortSpec, ParameterSpec, DataType, ExecutionContext, NodeData, BaseNode
from ..models.database import get_database
from ..models.schemas import ContextStorage


class ContextStoreNode(BaseNode):
    """Store conversation context by key for later retrieval."""
    
    NODE_TYPE = "context_store"
    
    def __init__(self):
        spec = NodeSpec(
            node_type=self.NODE_TYPE,
            display_name="Context Store",
            description="Store conversation context by key for stateless AI providers",
            category="AI/Context",
            inputs=[
                PortSpec(
                    name="context_key",
                    data_type=DataType.TEXT,
                    required=False,
                    description="Unique key to store context under (auto-generated if empty)"
                ),
                PortSpec(
                    name="context_data",
                    data_type=DataType.JSON,
                    description="Context data to store (messages, state, etc.)"
                ),
                PortSpec(
                    name="metadata",
                    data_type=DataType.JSON,
                    required=False,
                    description="Optional metadata (provider, model, etc.)"
                )
            ],
            outputs=[
                PortSpec(
                    name="stored_key",
                    data_type=DataType.TEXT,
                    description="The key where context was stored"
                ),
                PortSpec(
                    name="success",
                    data_type=DataType.TEXT,
                    description="Success status"
                )
            ],
            parameters=[
                ParameterSpec(
                    name="expires_hours",
                    data_type="number",
                    default=24,
                    description="Hours until context expires (0 = never)",
                    constraints={"min": 0, "max": 8760}  # Max 1 year
                ),
                ParameterSpec(
                    name="overwrite",
                    data_type="boolean", 
                    default=True,
                    description="Allow overwriting existing context"
                )
            ]
        )
        super().__init__(spec)
    
    async def execute(self, context: ExecutionContext, node_data: NodeData) -> Dict[str, Any]:
        """Execute context store node."""
        # Get inputs
        context_key = context.get_input_value(node_data.node_id, "context_key", "")
        context_data = context.get_input_value(node_data.node_id, "context_data")
        metadata = context.get_input_value(node_data.node_id, "metadata", {})
        
        # Get parameters
        expires_hours = node_data.parameters.get("expires_hours", 24)
        overwrite = node_data.parameters.get("overwrite", True)
        
        # Auto-generate key if not provided
        if not context_key:
            import uuid
            context_key = f"ctx_{uuid.uuid4().hex[:12]}"
        
        if not context_data:
            return {"error": "context_data is required"}
        
        try:
            # Get database session
            db = next(get_database())
            
            # Check if key already exists
            existing = db.query(ContextStorage).filter(
                ContextStorage.context_key == context_key
            ).first()
            
            if existing and not overwrite:
                return {
                    "error": f"Context key '{context_key}' already exists and overwrite is disabled",
                    "stored_key": context_key,
                    "success": "false"
                }
            
            # Calculate expiration
            expires_at = None
            if expires_hours > 0:
                expires_at = datetime.utcnow() + timedelta(hours=expires_hours)
            
            if existing:
                # Update existing
                existing.context_data = context_data
                existing.meta_data = metadata
                existing.updated_at = datetime.utcnow()
                existing.expires_at = expires_at
            else:
                # Create new
                context_record = ContextStorage(
                    context_key=context_key,
                    context_data=context_data,
                    meta_data=metadata,
                    expires_at=expires_at,
                    created_by="system"  # TODO: Get from auth
                )
                db.add(context_record)
            
            db.commit()
            db.close()
            
            return {
                "stored_key": context_key,
                "success": "true"
            }
            
        except Exception as e:
            return {
                "error": f"Failed to store context: {str(e)}",
                "stored_key": context_key,
                "success": "false"
            }


class ContextRetrieveNode(BaseNode):
    """Retrieve conversation context by key."""
    
    NODE_TYPE = "context_retrieve"
    
    def __init__(self):
        spec = NodeSpec(
            node_type=self.NODE_TYPE,
            display_name="Context Retrieve",
            description="Retrieve stored conversation context by key",
            category="AI/Context",
            inputs=[
                PortSpec(
                    name="context_key",
                    data_type=DataType.TEXT,
                    description="Key to retrieve context from"
                )
            ],
            outputs=[
                PortSpec(
                    name="context_data",
                    data_type=DataType.JSON,
                    description="Retrieved context data"
                ),
                PortSpec(
                    name="metadata",
                    data_type=DataType.JSON,
                    description="Context metadata"
                ),
                PortSpec(
                    name="found",
                    data_type=DataType.TEXT,
                    description="Whether context was found (true/false)"
                ),
                PortSpec(
                    name="age_hours",
                    data_type=DataType.TEXT,
                    description="Age of context in hours"
                )
            ],
            parameters=[
                ParameterSpec(
                    name="default_context",
                    data_type="json",
                    default={},
                    description="Default context to return if key not found"
                ),
                ParameterSpec(
                    name="cleanup_expired",
                    data_type="boolean",
                    default=True,
                    description="Automatically clean up expired contexts"
                )
            ]
        )
        super().__init__(spec)
    
    async def execute(self, context: ExecutionContext, node_data: NodeData) -> Dict[str, Any]:
        """Execute context retrieve node."""
        # Get inputs
        context_key = context.get_input_value(node_data.node_id, "context_key")
        
        # Get parameters
        default_context = node_data.parameters.get("default_context", {})
        cleanup_expired = node_data.parameters.get("cleanup_expired", True)
        
        if not context_key:
            return {
                "error": "context_key is required",
                "context_data": default_context,
                "metadata": {},
                "found": "false",
                "age_hours": "0"
            }
        
        try:
            # Get database session
            db = next(get_database())
            
            # Clean up expired contexts first
            if cleanup_expired:
                db.query(ContextStorage).filter(
                    ContextStorage.expires_at.isnot(None),
                    ContextStorage.expires_at < datetime.utcnow()
                ).delete()
                db.commit()
            
            # Find context
            context_record = db.query(ContextStorage).filter(
                ContextStorage.context_key == context_key
            ).first()
            
            if not context_record:
                db.close()
                return {
                    "context_data": default_context,
                    "metadata": {},
                    "found": "false",
                    "age_hours": "0"
                }
            
            # Check if expired
            if (context_record.expires_at and 
                context_record.expires_at < datetime.utcnow()):
                # Context expired, delete it
                db.delete(context_record)
                db.commit()
                db.close()
                return {
                    "context_data": default_context,
                    "metadata": {},
                    "found": "false",
                    "age_hours": "expired"
                }
            
            # Calculate age
            age = datetime.utcnow() - context_record.created_at
            age_hours = str(round(age.total_seconds() / 3600, 1))
            
            result = {
                "context_data": context_record.context_data,
                "metadata": context_record.meta_data or {},
                "found": "true",
                "age_hours": age_hours
            }
            
            db.close()
            return result
            
        except Exception as e:
            return {
                "error": f"Failed to retrieve context: {str(e)}",
                "context_data": default_context,
                "metadata": {},
                "found": "false",
                "age_hours": "0"
            }


class ContextListNode(BaseNode):
    """List stored contexts with optional filtering."""
    
    NODE_TYPE = "context_list"
    
    def __init__(self):
        spec = NodeSpec(
            node_type=self.NODE_TYPE,
            display_name="Context List",
            description="List all stored contexts with metadata",
            category="AI/Context",
            inputs=[
                PortSpec(
                    name="pattern",
                    data_type=DataType.TEXT,
                    required=False,
                    description="Optional pattern to filter context keys"
                )
            ],
            outputs=[
                PortSpec(
                    name="contexts",
                    data_type=DataType.JSON,
                    description="List of context information"
                ),
                PortSpec(
                    name="count",
                    data_type=DataType.TEXT,
                    description="Number of contexts found"
                )
            ],
            parameters=[
                ParameterSpec(
                    name="include_expired",
                    data_type="boolean",
                    default=False,
                    description="Include expired contexts in list"
                ),
                ParameterSpec(
                    name="limit",
                    data_type="number",
                    default=50,
                    description="Maximum contexts to return",
                    constraints={"min": 1, "max": 1000}
                )
            ]
        )
        super().__init__(spec)
    
    async def execute(self, context: ExecutionContext, node_data: NodeData) -> Dict[str, Any]:
        """Execute context list node."""
        # Get inputs
        pattern = context.get_input_value(node_data.node_id, "pattern", "")
        
        # Get parameters
        include_expired = node_data.parameters.get("include_expired", False)
        limit = node_data.parameters.get("limit", 50)
        
        try:
            # Get database session
            db = next(get_database())
            
            # Build query
            query = db.query(ContextStorage)
            
            # Filter by pattern if provided
            if pattern:
                query = query.filter(ContextStorage.context_key.contains(pattern))
            
            # Filter expired contexts
            if not include_expired:
                query = query.filter(
                    (ContextStorage.expires_at.is_(None)) |
                    (ContextStorage.expires_at > datetime.utcnow())
                )
            
            # Apply limit and get results
            contexts_raw = query.limit(limit).all()
            
            # Format results
            contexts = []
            now = datetime.utcnow()
            
            for ctx in contexts_raw:
                age = now - ctx.created_at
                age_hours = round(age.total_seconds() / 3600, 1)
                
                is_expired = (ctx.expires_at and ctx.expires_at < now)
                
                contexts.append({
                    "context_key": ctx.context_key,
                    "age_hours": age_hours,
                    "created_at": ctx.created_at.isoformat(),
                    "updated_at": ctx.updated_at.isoformat(),
                    "expires_at": ctx.expires_at.isoformat() if ctx.expires_at else None,
                    "is_expired": is_expired,
                    "metadata": ctx.meta_data or {},
                    "data_size": len(str(ctx.context_data))
                })
            
            db.close()
            
            return {
                "contexts": contexts,
                "count": str(len(contexts))
            }
            
        except Exception as e:
            return {
                "error": f"Failed to list contexts: {str(e)}",
                "contexts": [],
                "count": "0"
            }


class GenerateRandomKeyNode(BaseNode):
    """Generate random keys for context storage and other uses."""
    
    NODE_TYPE = "generate_random_key"
    
    def __init__(self):
        spec = NodeSpec(
            node_type=self.NODE_TYPE,
            display_name="Generate Random Key",
            description="Generate random keys with optional seed data (invocation-triggered)",
            category="Utilities",
            inputs=[
                PortSpec(
                    name="seed_data",
                    data_type=DataType.TEXT,
                    required=False,
                    description="Optional seed data to influence key generation"
                )
            ],
            outputs=[
                PortSpec(
                    name="random_key",
                    data_type=DataType.TEXT,
                    description="Generated random key"
                ),
                PortSpec(
                    name="short_key",
                    data_type=DataType.TEXT,
                    description="Shorter 8-character version"
                ),
                PortSpec(
                    name="hash_key",
                    data_type=DataType.TEXT,
                    description="Hash-based key if seed provided"
                )
            ],
            parameters=[
                ParameterSpec(
                    name="key_format",
                    data_type="select",
                    default="uuid",
                    description="Format for random key generation",
                    constraints={"options": ["uuid", "hex", "alphanumeric", "words"]}
                ),
                ParameterSpec(
                    name="key_length",
                    data_type="number",
                    default=12,
                    description="Length of generated key (for hex/alphanumeric)",
                    constraints={"min": 4, "max": 64}
                ),
                ParameterSpec(
                    name="prefix",
                    data_type="string",
                    default="key_",
                    description="Prefix for generated keys"
                ),
                ParameterSpec(
                    name="include_timestamp",
                    data_type="boolean",
                    default=False,
                    description="Include timestamp component in key"
                )
            ]
        )
        super().__init__(spec)
    
    async def execute(self, context: ExecutionContext, node_data: NodeData) -> Dict[str, Any]:
        """Execute random key generation."""
        # Get inputs
        seed_data = context.get_input_value(node_data.node_id, "seed_data", "")
        
        # Get parameters
        key_format = node_data.parameters.get("key_format", "uuid")
        key_length = int(node_data.parameters.get("key_length", 12))
        prefix = node_data.parameters.get("prefix", "key_")
        include_timestamp = node_data.parameters.get("include_timestamp", False)
        
        try:
            # Generate timestamp component if requested
            timestamp_part = ""
            if include_timestamp:
                timestamp_part = f"_{int(datetime.utcnow().timestamp())}"
            
            # Generate main random key based on format
            if key_format == "uuid":
                main_key = uuid.uuid4().hex[:key_length]
            elif key_format == "hex":
                main_key = ''.join(random.choices('0123456789abcdef', k=key_length))
            elif key_format == "alphanumeric":
                chars = string.ascii_lowercase + string.digits
                main_key = ''.join(random.choices(chars, k=key_length))
            elif key_format == "words":
                # Simple word-based key (could be expanded with word lists)
                words = ['red', 'blue', 'fast', 'slow', 'big', 'small', 'hot', 'cold', 
                        'cat', 'dog', 'sun', 'moon', 'tree', 'rock', 'wave', 'star']
                selected_words = random.sample(words, min(3, key_length // 3 + 1))
                main_key = '-'.join(selected_words)
                if len(main_key) > key_length:
                    main_key = main_key[:key_length]
            else:
                main_key = uuid.uuid4().hex[:key_length]
            
            # Construct final random key
            random_key = f"{prefix}{main_key}{timestamp_part}"
            
            # Generate short key (always 8 chars)
            short_key = f"{prefix}{uuid.uuid4().hex[:8]}"
            
            # Generate hash-based key if seed data provided
            hash_key = ""
            if seed_data:
                # Create deterministic hash from seed
                hash_obj = hashlib.sha256(seed_data.encode('utf-8'))
                hash_hex = hash_obj.hexdigest()[:key_length]
                hash_key = f"{prefix}{hash_hex}{timestamp_part}"
            else:
                # No seed, use random for hash key too
                hash_key = random_key
            
            return {
                "random_key": random_key,
                "short_key": short_key,
                "hash_key": hash_key
            }
            
        except Exception as e:
            # Fallback to simple UUID if anything goes wrong
            fallback_key = f"{prefix}{uuid.uuid4().hex[:12]}"
            return {
                "random_key": fallback_key,
                "short_key": fallback_key[:16],
                "hash_key": fallback_key,
                "error": f"Key generation error: {str(e)}"
            }


# Register nodes
CONTEXT_NODES = {
    "context_store": ContextStoreNode,
    "context_retrieve": ContextRetrieveNode,
    "context_list": ContextListNode,
    "generate_random_key": GenerateRandomKeyNode,
}