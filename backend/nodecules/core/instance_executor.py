"""Graph Instance Executor for persistent multi-run execution."""

import logging
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4
from sqlalchemy.orm import Session

from .executor import GraphExecutor
from .types import GraphData, ExecutionContext, NodeStatus
from ..models.database import get_database
from ..models.schemas import Graph
from ..models.instance import GraphInstance, InstanceExecution, InstanceStateManager

logger = logging.getLogger(__name__)


class InstanceExecutionContext(ExecutionContext):
    """Extended execution context with instance state access."""
    
    def __init__(self, instance: GraphInstance, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance = instance
        self.state_manager = InstanceStateManager()
    
    def get_instance_state(self, key: str, default: Any = None) -> Any:
        """Get value from persistent instance state."""
        return self.state_manager.get_state(self.instance, key, default)
    
    def set_instance_state(self, key: str, value: Any) -> None:
        """Set value in persistent instance state."""
        self.state_manager.set_state(self.instance, key, value)
    
    def increment_instance_counter(self, key: str = "default", amount: int = 1) -> int:
        """Increment counter in instance state."""
        return self.state_manager.increment_counter(self.instance, key, amount)
    
    def append_to_instance_list(self, key: str, item: Any) -> list:
        """Append to list in instance state."""
        return self.state_manager.append_to_list(self.instance, key, item)


class GraphInstanceExecutor:
    """Executor for persistent graph instances."""
    
    def __init__(self, node_registry: Dict[str, type]):
        self.executor = GraphExecutor(node_registry)
    
    def create_instance(
        self, 
        db: Session,
        graph_id: str, 
        instance_name: Optional[str] = None,
        description: Optional[str] = None
    ) -> str:
        """Create a new graph instance."""
        
        # Generate instance ID
        instance_id = f"gi_{str(uuid4())[:8]}"
        
        # Get graph - try by UUID first, then by name
        try:
            from uuid import UUID
            # Try as UUID first
            graph_uuid = UUID(graph_id)
            graph = db.query(Graph).filter(Graph.id == graph_uuid).first()
        except ValueError:
            # Not a UUID, try by name
            graph = db.query(Graph).filter(Graph.name == graph_id).first()
        
        if not graph:
            raise ValueError(f"Graph not found: {graph_id}")
        
        # Create instance
        instance = GraphInstance(
            instance_id=instance_id,
            graph_id=graph.id,
            name=instance_name or f"Instance of {graph.name}",
            description=description,
            instance_state={},
            run_count=0
        )
        
        db.add(instance)
        db.commit()
        
        logger.info(f"Created graph instance {instance_id} for graph {graph.name}")
        return instance_id
    
    async def execute_instance(
        self,
        db: Session,
        instance_id: str,
        inputs: Dict[str, Any]
    ) -> InstanceExecution:
        """Execute a graph instance with persistent state."""
        
        # Load instance
        instance = db.query(GraphInstance).filter(
            GraphInstance.instance_id == instance_id,
            GraphInstance.is_active == True
        ).first()
        
        if not instance:
            raise ValueError(f"Instance not found: {instance_id}")
        
        # Load graph
        graph = db.query(Graph).filter(Graph.id == instance.graph_id).first()
        if not graph:
            raise ValueError(f"Graph not found for instance: {instance_id}")
        
        # Convert to GraphData
        graph_data = self._graph_to_data(graph)
        
        # Create execution record
        execution_id = str(uuid4())
        execution_record = InstanceExecution(
            instance_id=instance.id,
            execution_id=execution_id,
            inputs=inputs,
            started_at=datetime.utcnow(),
            status="running"
        )
        
        db.add(execution_record)
        db.commit()
        
        try:
            # Inject previous context IDs from last execution
            enhanced_inputs = inputs.copy()
            
            if instance.last_outputs and instance.run_count > 0:
                # Find any context keys from previous run and inject them
                for node_id, node_outputs in instance.last_outputs.items():
                    if isinstance(node_outputs, dict):
                        # Handle both old context_id and new context_key
                        # Store node-specific context keys only - no global bleeding
                        if 'context_key' in node_outputs:
                            enhanced_inputs[f"_context_key_{node_id}"] = node_outputs['context_key']
                        elif 'context_id' in node_outputs:
                            enhanced_inputs[f"_context_id_{node_id}"] = node_outputs['context_id']
            
            # Create instance-aware execution context
            context = InstanceExecutionContext(
                instance=instance,
                execution_id=execution_id,
                graph=graph_data,
                execution_inputs=enhanced_inputs,
                started_at=datetime.utcnow()
            )
            
            # Execute the graph
            logger.info(f"Executing instance {instance_id}, run #{instance.run_count + 1}")
            final_context = await self.executor.execute_graph_with_context(context)
            
            # Update instance state
            instance.run_count += 1
            instance.last_executed = datetime.utcnow()
            instance.last_outputs = final_context.node_outputs
            
            # Update execution record
            execution_record.outputs = final_context.node_outputs
            execution_record.node_status = {k: v.value for k, v in final_context.node_status.items()}
            execution_record.errors = final_context.errors
            execution_record.completed_at = datetime.utcnow()
            execution_record.status = "completed" if not final_context.errors else "failed"
            
            db.commit()
            
            logger.info(f"Instance {instance_id} execution completed")
            return execution_record
            
        except Exception as e:
            # Update execution record with error
            execution_record.status = "failed"
            execution_record.errors = {"system": str(e)}
            execution_record.completed_at = datetime.utcnow()
            
            db.commit()
            
            logger.error(f"Instance {instance_id} execution failed: {e}")
            raise
    
    def get_instance_info(self, db: Session, instance_id: str) -> Optional[Dict[str, Any]]:
        """Get instance information and state."""
        instance = db.query(GraphInstance).filter(
            GraphInstance.instance_id == instance_id,
            GraphInstance.is_active == True
        ).first()
        
        if not instance:
            return None
        
        return {
            "instance_id": instance.instance_id,
            "graph_id": str(instance.graph_id),
            "name": instance.name,
            "description": instance.description,
            "state": instance.instance_state,
            "run_count": instance.run_count,
            "created_at": instance.created_at.isoformat(),
            "last_executed": instance.last_executed.isoformat() if instance.last_executed else None,
            "last_outputs": instance.last_outputs
        }
    
    def reset_instance(self, db: Session, instance_id: str, keys: Optional[list] = None) -> bool:
        """Reset instance state (specific keys or all)."""
        instance = db.query(GraphInstance).filter(
            GraphInstance.instance_id == instance_id,
            GraphInstance.is_active == True
        ).first()
        
        if not instance:
            return False
        
        if keys is None:
            # Reset all state
            instance.instance_state = {}
            instance.run_count = 0
            instance.last_outputs = None
        else:
            # Reset specific keys
            for key in keys:
                InstanceStateManager.clear_state(instance, key)
        
        db.commit()
        
        logger.info(f"Reset instance {instance_id} state")
        return True
    
    def delete_instance(self, db: Session, instance_id: str) -> bool:
        """Delete a graph instance."""
        instance = db.query(GraphInstance).filter(
            GraphInstance.instance_id == instance_id
        ).first()
        
        if not instance:
            return False
        
        instance.is_active = False
        db.commit()
        
        logger.info(f"Deleted instance {instance_id}")
        return True
    
    def _graph_to_data(self, graph: Graph) -> GraphData:
        """Convert database Graph to GraphData."""
        from ..core.types import GraphData, NodeData, EdgeData
        
        # Convert nodes
        nodes = {}
        for node_id, node_info in graph.nodes.items():
            nodes[node_id] = NodeData(
                node_id=node_info["node_id"],
                node_type=node_info["node_type"],
                position=node_info.get("position", {}),
                parameters=node_info.get("parameters", {})
            )
        
        # Convert edges
        edges = []
        for edge_info in graph.edges:
            edges.append(EdgeData(
                edge_id=edge_info["edge_id"],
                source_node=edge_info["source_node"],
                source_port=edge_info["source_port"],
                target_node=edge_info["target_node"],
                target_port=edge_info["target_port"]
            ))
        
        return GraphData(
            graph_id=str(graph.id),
            name=graph.name,
            nodes=nodes,
            edges=edges,
            meta_data=graph.meta_data or {}
        )