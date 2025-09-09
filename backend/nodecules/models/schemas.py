"""SQLAlchemy database models."""

import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, JSON, Integer, Boolean, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from .database import Base


class Graph(Base):
    """Graph storage model."""
    
    __tablename__ = "graphs"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    nodes = Column(JSON, nullable=False, default=dict)
    edges = Column(JSON, nullable=False, default=list)
    meta_data = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(String(255))  # User ID
    
    # Relationships
    executions = relationship("Execution", back_populates="graph")


class Execution(Base):
    """Execution history model."""
    
    __tablename__ = "executions"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    graph_id = Column(UUID(as_uuid=True), ForeignKey("graphs.id"), nullable=False)
    status = Column(String(50), nullable=False, default="pending")  # pending, running, completed, failed
    inputs = Column(JSON, default=dict)
    outputs = Column(JSON, default=dict)
    node_status = Column(JSON, default=dict)  # Status of each node
    errors = Column(JSON, default=dict)  # Error messages per node
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    graph = relationship("Graph", back_populates="executions")


class DataObject(Base):
    """Data object storage model."""
    
    __tablename__ = "data_objects"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    object_type = Column(String(50), nullable=False)  # text, image, audio, video, file
    content_hash = Column(String(64), nullable=False, unique=True)
    storage_location = Column(String(500))  # File path or URL
    file_size = Column(Integer)
    mime_type = Column(String(100))
    meta_data = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(String(255))  # User ID
    
    # Relationships
    annotations = relationship("Annotation", back_populates="data_object")


class Annotation(Base):
    """Annotation model."""
    
    __tablename__ = "annotations"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    data_object_id = Column(UUID(as_uuid=True), ForeignKey("data_objects.id"), nullable=False)
    annotation_type = Column(String(50), nullable=False)  # classification, extraction, summary
    content = Column(JSON, nullable=False)
    confidence = Column(Integer)  # 0-100
    annotator_id = Column(String(255))  # User or system ID
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    data_object = relationship("DataObject", back_populates="annotations")


class User(Base):
    """User model (basic implementation)."""
    
    __tablename__ = "users"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String(100), unique=True, nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255))
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ContextStorage(Base):
    """Context storage for conversation continuity."""
    
    __tablename__ = "context_storage"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    context_key = Column(String(255), nullable=False, unique=True, index=True)
    context_data = Column(JSON, nullable=False, default=dict)
    meta_data = Column(JSON, default=dict)  # Store provider info, model, etc.
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    expires_at = Column(DateTime)  # Optional expiration
    created_by = Column(String(255))  # User ID