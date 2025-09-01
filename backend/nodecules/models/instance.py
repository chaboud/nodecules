"""Graph Instance models for persistent multi-run execution."""

from datetime import datetime
from typing import Dict, Any, Optional
from uuid import uuid4
from sqlalchemy import Column, String, DateTime, JSON, Boolean, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import relationship

from .database import Base


class GraphInstance(Base):
    """Persistent graph instance for multi-run execution."""
    
    __tablename__ = "graph_instances"
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    instance_id = Column(String(100), unique=True, nullable=False, index=True)
    graph_id = Column(PG_UUID(as_uuid=True), nullable=False)
    
    # Instance metadata
    name = Column(String(200), nullable=True)
    description = Column(Text, nullable=True)
    
    # Persistent state storage
    instance_state = Column(JSON, nullable=False, default=dict)  # Cross-execution state
    last_outputs = Column(JSON, nullable=True)  # Outputs from last execution
    
    # Execution tracking
    run_count = Column(JSON, nullable=False, default=0)  # How many times executed
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_executed = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    
    # Relationships
    executions = relationship("InstanceExecution", back_populates="instance", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<GraphInstance(id='{self.instance_id}', graph_id='{self.graph_id}', runs={self.run_count})>"


class InstanceExecution(Base):
    """Individual execution run within a graph instance."""
    
    __tablename__ = "instance_executions"
    
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    instance_id = Column(PG_UUID(as_uuid=True), ForeignKey('graph_instances.id'), nullable=False)
    execution_id = Column(String(100), nullable=False, index=True)
    
    # Execution data
    inputs = Column(JSON, nullable=False, default=dict)
    outputs = Column(JSON, nullable=True)
    node_status = Column(JSON, nullable=True) 
    errors = Column(JSON, nullable=True)
    
    # Timing
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Status
    status = Column(String(50), default="pending", nullable=False)  # pending, running, completed, failed
    
    # Relationships
    instance = relationship("GraphInstance", back_populates="executions")
    
    def __repr__(self):
        return f"<InstanceExecution(execution_id='{self.execution_id}', status='{self.status}')>"


class InstanceStateManager:
    """Manages state for graph instances."""
    
    @staticmethod
    def get_state_key(instance_id: str, key: str) -> str:
        """Generate namespaced state key."""
        return f"instance:{instance_id}:{key}"
    
    @staticmethod
    def set_state(instance: GraphInstance, key: str, value: Any) -> None:
        """Set state value for instance."""
        if instance.instance_state is None:
            instance.instance_state = {}
        instance.instance_state[key] = value
    
    @staticmethod
    def get_state(instance: GraphInstance, key: str, default: Any = None) -> Any:
        """Get state value from instance."""
        if not instance.instance_state:
            return default
        return instance.instance_state.get(key, default)
    
    @staticmethod
    def clear_state(instance: GraphInstance, key: Optional[str] = None) -> None:
        """Clear state (specific key or all)."""
        if key is None:
            instance.instance_state = {}
        elif instance.instance_state:
            instance.instance_state.pop(key, None)
    
    @staticmethod
    def increment_counter(instance: GraphInstance, key: str = "default", amount: int = 1) -> int:
        """Increment a counter in instance state."""
        current = InstanceStateManager.get_state(instance, f"counter:{key}", 0)
        new_value = current + amount
        InstanceStateManager.set_state(instance, f"counter:{key}", new_value)
        return new_value
    
    @staticmethod
    def append_to_list(instance: GraphInstance, key: str, item: Any) -> list:
        """Append item to a list in instance state."""
        current_list = InstanceStateManager.get_state(instance, f"list:{key}", [])
        current_list.append(item)
        InstanceStateManager.set_state(instance, f"list:{key}", current_list)
        return current_list
    
    @staticmethod
    def update_dict(instance: GraphInstance, key: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update dictionary in instance state."""
        current_dict = InstanceStateManager.get_state(instance, f"dict:{key}", {})
        current_dict.update(updates)
        InstanceStateManager.set_state(instance, f"dict:{key}", current_dict)
        return current_dict