"""API routes for plugin and node type information."""

import logging
from typing import List
from fastapi import APIRouter, Request

from .models import NodeSpecResponse, PortSpecResponse, ParameterSpecResponse

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/nodes", response_model=List[NodeSpecResponse])
async def get_available_nodes(request: Request):
    """Get all available node types."""
    try:
        node_registry = request.app.state.node_registry
        node_specs = []
        
        # Get specs from all registered node types
        for node_type in node_registry.list_types():
            node_class = node_registry.get(node_type)
            if node_class:
                try:
                    instance = node_class()
                    node_specs.append(instance.spec)
                except Exception as e:
                    logger.error(f"Failed to create instance for node type {node_type}: {e}")
        
        # Convert to response format
        response_specs = []
        for spec in node_specs:
            inputs = [
                PortSpecResponse(
                    name=port.name,
                    data_type=port.data_type.value,
                    required=port.required,
                    default=port.default,
                    description=port.description
                ) for port in spec.inputs
            ]
            
            outputs = [
                PortSpecResponse(
                    name=port.name,
                    data_type=port.data_type.value,
                    required=port.required,
                    default=port.default,
                    description=port.description
                ) for port in spec.outputs
            ]
            
            parameters = [
                ParameterSpecResponse(
                    name=param.name,
                    data_type=param.data_type,
                    default=param.default,
                    description=param.description,
                    constraints=param.constraints
                ) for param in spec.parameters
            ]
            
            response_specs.append(NodeSpecResponse(
                node_type=spec.node_type,
                display_name=spec.display_name,
                description=spec.description,
                category=spec.category,
                inputs=inputs,
                outputs=outputs,
                parameters=parameters
            ))
        
        return response_specs
        
    except Exception as e:
        logger.error(f"Failed to get available nodes: {e}")
        return []


@router.get("/nodes/{node_type}", response_model=NodeSpecResponse)
async def get_node_spec(node_type: str, request: Request):
    """Get specification for a specific node type."""
    try:
        node_registry = request.app.state.node_registry
        node_class = node_registry.get(node_type)
        
        if node_class:
            try:
                instance = node_class()
                spec = instance.spec
                inputs = [
                    PortSpecResponse(
                        name=port.name,
                        data_type=port.data_type.value,
                        required=port.required,
                        default=port.default,
                        description=port.description
                    ) for port in spec.inputs
                ]
                
                outputs = [
                    PortSpecResponse(
                        name=port.name,
                        data_type=port.data_type.value,
                        required=port.required,
                        default=port.default,
                        description=port.description
                    ) for port in spec.outputs
                ]
                
                parameters = [
                    ParameterSpecResponse(
                        name=param.name,
                        data_type=param.data_type,
                        default=param.default,
                        description=param.description,
                        constraints=param.constraints
                    ) for param in spec.parameters
                ]
                
                return NodeSpecResponse(
                    node_type=spec.node_type,
                    display_name=spec.display_name,
                    description=spec.description,
                    category=spec.category,
                    inputs=inputs,
                    outputs=outputs,
                    parameters=parameters
                )
            except Exception as e:
                logger.error(f"Failed to create instance for node type {node_type}: {e}")
                from fastapi import HTTPException
                raise HTTPException(status_code=500, detail=str(e))
        
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Node type '{node_type}' not found")
        
    except Exception as e:
        logger.error(f"Failed to get node spec for {node_type}: {e}")
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))