"""API routes for graph instances."""

import logging
from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from ..models.database import get_database
from ..core.instance_executor import GraphInstanceExecutor
from ..plugins.builtin_nodes import BUILTIN_NODES

router = APIRouter()
logger = logging.getLogger(__name__)


class InstanceCreateRequest(BaseModel):
    """Request to create a graph instance."""
    graph_id: str
    name: Optional[str] = None
    description: Optional[str] = None


class InstanceExecuteRequest(BaseModel):
    """Request to execute a graph instance."""
    inputs: dict = Field(default_factory=dict)


class InstanceResponse(BaseModel):
    """Graph instance response."""
    instance_id: str
    graph_id: str
    name: str
    description: Optional[str]
    state: dict
    run_count: int
    created_at: str
    last_executed: Optional[str]
    last_outputs: Optional[dict]


class InstanceExecutionResponse(BaseModel):
    """Instance execution response."""
    execution_id: str
    instance_id: str
    inputs: dict
    outputs: Optional[dict]
    node_status: Optional[dict]
    errors: Optional[dict]
    status: str
    started_at: Optional[str]
    completed_at: Optional[str]
    
    @classmethod
    def from_execution(cls, execution):
        return cls(
            execution_id=execution.execution_id,
            instance_id=execution.instance.instance_id,
            inputs=execution.inputs,
            outputs=execution.outputs,
            node_status=execution.node_status,
            errors=execution.errors,
            status=execution.status,
            started_at=execution.started_at.isoformat() if execution.started_at else None,
            completed_at=execution.completed_at.isoformat() if execution.completed_at else None
        )


# Global instance executor
instance_executor = GraphInstanceExecutor(BUILTIN_NODES)


@router.post("/", response_model=dict)
async def create_instance(
    request: InstanceCreateRequest,
    db: Session = Depends(get_database)
):
    """Create a new graph instance."""
    try:
        instance_id = instance_executor.create_instance(
            db=db,
            graph_id=request.graph_id,
            instance_name=request.name,
            description=request.description
        )
        
        return {
            "instance_id": instance_id,
            "message": "Instance created successfully"
        }
        
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating instance: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/{instance_id}", response_model=InstanceResponse)
async def get_instance(
    instance_id: str,
    db: Session = Depends(get_database)
):
    """Get instance information."""
    info = instance_executor.get_instance_info(db, instance_id)
    
    if not info:
        raise HTTPException(status_code=404, detail="Instance not found")
    
    return InstanceResponse(**info)


@router.post("/{instance_id}/execute", response_model=InstanceExecutionResponse)
async def execute_instance(
    instance_id: str,
    request: InstanceExecuteRequest,
    db: Session = Depends(get_database)
):
    """Execute a graph instance."""
    try:
        execution = await instance_executor.execute_instance(
            db=db,
            instance_id=instance_id,
            inputs=request.inputs
        )
        
        return InstanceExecutionResponse.from_execution(execution)
        
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error executing instance {instance_id}: {e}")
        raise HTTPException(status_code=500, detail="Execution failed")


@router.post("/{instance_id}/reset", response_model=dict)
async def reset_instance(
    instance_id: str,
    keys: Optional[List[str]] = None,
    db: Session = Depends(get_database)
):
    """Reset instance state."""
    success = instance_executor.reset_instance(db, instance_id, keys)
    
    if not success:
        raise HTTPException(status_code=404, detail="Instance not found")
    
    return {
        "message": f"Instance {instance_id} reset successfully",
        "keys_reset": keys or "all"
    }


@router.delete("/{instance_id}", response_model=dict)
async def delete_instance(
    instance_id: str,
    db: Session = Depends(get_database)
):
    """Delete a graph instance."""
    success = instance_executor.delete_instance(db, instance_id)
    
    if not success:
        raise HTTPException(status_code=404, detail="Instance not found")
    
    return {"message": f"Instance {instance_id} deleted successfully"}


@router.get("/", response_model=List[InstanceResponse])
async def list_instances(
    db: Session = Depends(get_database),
    limit: int = 50
):
    """List all active graph instances."""
    from ..models.instance import GraphInstance
    
    instances = db.query(GraphInstance).filter(
        GraphInstance.is_active == True
    ).limit(limit).all()
    
    result = []
    for instance in instances:
        info = instance_executor.get_instance_info(db, instance.instance_id)
        if info:
            result.append(InstanceResponse(**info))
    
    return result