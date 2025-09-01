"""Graph execution engine."""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, AsyncGenerator

from .graph import GraphExecutionPlanner
from .types import BaseNode, ExecutionContext, GraphData, NodeStatus


logger = logging.getLogger(__name__)


class ExecutionError(Exception):
    """Raised when execution fails."""
    pass


class GraphExecutor:
    """Executes graphs using topological sort."""
    
    def __init__(self, node_registry: Dict[str, type[BaseNode]]):
        self.node_registry = node_registry
        
    async def execute_graph(self, graph: GraphData, inputs: Optional[Dict[str, Any]] = None) -> ExecutionContext:
        """Execute a complete graph."""
        context = ExecutionContext(
            execution_id="",  # Will be auto-generated
            graph=graph,
            execution_inputs=inputs or {},
            started_at=datetime.utcnow()
        )
        
        # Initialize all nodes as pending
        for node_id in graph.nodes:
            context.set_node_status(node_id, NodeStatus.PENDING)
            
        try:
            planner = GraphExecutionPlanner(graph)
            execution_order = planner.get_execution_order()
            
            logger.info(f"Executing graph {graph.graph_id} with {len(execution_order)} nodes")
            
            # Execute nodes in order
            for node_id in execution_order:
                await self._execute_node(context, node_id)
                
            context.completed_at = datetime.utcnow()
            logger.info(f"Graph {graph.graph_id} execution completed")
            
        except Exception as e:
            context.completed_at = datetime.utcnow()
            logger.error(f"Graph {graph.graph_id} execution failed: {e}")
            raise ExecutionError(f"Graph execution failed: {e}") from e
            
        return context
    
    async def execute_graph_with_context(self, context: ExecutionContext) -> ExecutionContext:
        """Execute a graph with an existing context (for instance execution)."""
        # Initialize all nodes as pending
        for node_id in context.graph.nodes:
            context.set_node_status(node_id, NodeStatus.PENDING)
            
        try:
            planner = GraphExecutionPlanner(context.graph)
            execution_order = planner.get_execution_order()
            
            logger.info(f"Executing graph {context.graph.graph_id} with {len(execution_order)} nodes")
            
            # Execute nodes in order
            for node_id in execution_order:
                await self._execute_node(context, node_id)
                
            context.completed_at = datetime.utcnow()
            logger.info(f"Graph {context.graph.graph_id} execution completed")
            
        except Exception as e:
            context.completed_at = datetime.utcnow()
            logger.error(f"Graph {context.graph.graph_id} execution failed: {e}")
            raise ExecutionError(f"Graph execution failed: {e}") from e
            
        return context
        
    async def execute_parallel_batches(self, graph: GraphData, inputs: Optional[Dict[str, Any]] = None) -> ExecutionContext:
        """Execute graph with parallel batches."""
        context = ExecutionContext(
            execution_id="",  # Will be auto-generated
            graph=graph,
            started_at=datetime.utcnow()
        )
        
        # Initialize all nodes as pending
        for node_id in graph.nodes:
            context.set_node_status(node_id, NodeStatus.PENDING)
            
        try:
            planner = GraphExecutionPlanner(graph)
            batches = planner.get_parallel_batches()
            
            logger.info(f"Executing graph {graph.graph_id} with {len(batches)} batches")
            
            # Execute batches sequentially, nodes within batches in parallel
            for batch_idx, batch in enumerate(batches):
                logger.info(f"Executing batch {batch_idx + 1}/{len(batches)} with {len(batch)} nodes")
                
                # Execute all nodes in batch concurrently
                tasks = [self._execute_node(context, node_id) for node_id in batch]
                await asyncio.gather(*tasks)
                
            context.completed_at = datetime.utcnow()
            logger.info(f"Graph {graph.graph_id} execution completed")
            
        except Exception as e:
            context.completed_at = datetime.utcnow()
            logger.error(f"Graph {graph.graph_id} execution failed: {e}")
            raise ExecutionError(f"Graph execution failed: {e}") from e
            
        return context
        
    async def _execute_node(self, context: ExecutionContext, node_id: str) -> None:
        """Execute a single node."""
        node_data = context.graph.nodes[node_id]
        
        try:
            context.set_node_status(node_id, NodeStatus.RUNNING)
            logger.debug(f"Executing node {node_id} ({node_data.node_type})")
            
            # Get node implementation
            if node_data.node_type not in self.node_registry:
                raise ExecutionError(f"Unknown node type: {node_data.node_type}")
                
            node_class = self.node_registry[node_data.node_type]
            node_instance = node_class()
            
            # Collect inputs from connected nodes
            inputs = {}
            for port in node_instance.spec.inputs:
                value = context.get_input_value(node_id, port.name)
                logger.debug(f"Node {node_id} port {port.name}: value={value}, required={port.required}")
                if value is not None:
                    inputs[port.name] = value
                elif port.required and port.default is not None:
                    inputs[port.name] = port.default
                    
            logger.debug(f"Node {node_id} collected inputs: {inputs}")
            logger.debug(f"Node {node_id} required ports: {[p.name for p in node_instance.spec.inputs if p.required]}")
                    
            # Validate inputs
            if not node_instance.validate_inputs(inputs):
                missing_required = [p.name for p in node_instance.spec.inputs if p.required and p.name not in inputs]
                raise ExecutionError(f"Invalid inputs for node {node_id}. Missing required inputs: {missing_required}")
                
            # Execute node
            outputs = await node_instance.execute(context, node_data)
            
            # Store outputs
            for port_name, value in outputs.items():
                context.set_node_output(node_id, port_name, value)
                
            context.set_node_status(node_id, NodeStatus.COMPLETED)
            logger.debug(f"Node {node_id} completed successfully")
            
        except Exception as e:
            context.set_node_status(node_id, NodeStatus.FAILED)
            context.errors[node_id] = str(e)
            logger.error(f"Node {node_id} failed: {e}")
            raise ExecutionError(f"Node {node_id} failed: {e}") from e
    
    async def execute_graph_streaming(
        self, 
        graph: GraphData, 
        inputs: Optional[Dict[str, Any]] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Execute a graph with streaming for nodes that support it."""
        context = ExecutionContext(
            execution_id="",  # Will be auto-generated
            graph=graph,
            execution_inputs=inputs or {},
            started_at=datetime.utcnow()
        )
        
        # Initialize all nodes as pending
        for node_id in graph.nodes:
            context.set_node_status(node_id, NodeStatus.PENDING)
            
        try:
            planner = GraphExecutionPlanner(graph)
            execution_order = planner.get_execution_order()
            
            logger.info(f"Streaming execution of graph {graph.graph_id} with {len(execution_order)} nodes")
            
            # Execute nodes in order, yielding streaming updates
            for node_id in execution_order:
                node_data = graph.nodes[node_id]
                
                # Check if node supports streaming
                if self._node_supports_streaming(node_data):
                    # Stream this node's execution
                    async for chunk in self._execute_node_streaming(context, node_id):
                        yield {
                            "type": "node_chunk",
                            "node_id": node_id,
                            "chunk": chunk,
                            "timestamp": datetime.utcnow().isoformat()
                        }
                else:
                    # Execute normally
                    await self._execute_node(context, node_id)
                    
                # Yield node completion
                yield {
                    "type": "node_complete",
                    "node_id": node_id,
                    "status": context.node_status.get(node_id, NodeStatus.PENDING).value,
                    "outputs": context.node_outputs.get(node_id, {}),
                    "timestamp": datetime.utcnow().isoformat()
                }
                
            context.completed_at = datetime.utcnow()
            logger.info(f"Graph {graph.graph_id} streaming execution completed")
            
            # Final completion message
            yield {
                "type": "execution_complete",
                "status": "completed",
                "outputs": context.node_outputs,
                "timestamp": context.completed_at.isoformat()
            }
            
        except Exception as e:
            context.completed_at = datetime.utcnow()
            logger.error(f"Graph {graph.graph_id} streaming execution failed: {e}")
            
            yield {
                "type": "execution_error",
                "error": str(e),
                "timestamp": context.completed_at.isoformat()
            }
            raise ExecutionError(f"Graph streaming execution failed: {e}") from e
    
    def _node_supports_streaming(self, node_data) -> bool:
        """Check if a node supports streaming based on its parameters."""
        # Check for streaming parameter
        streaming_param = node_data.parameters.get("streaming", False)
        
        # Also check for streaming-capable node types
        streaming_node_types = {"immutable_chat", "smart_chat"}
        
        return (streaming_param or node_data.node_type in streaming_node_types)
    
    async def _execute_node_streaming(self, context: ExecutionContext, node_id: str) -> AsyncGenerator[str, None]:
        """Execute a node with streaming support."""
        node_data = context.graph.nodes[node_id]
        
        try:
            context.set_node_status(node_id, NodeStatus.RUNNING)
            logger.debug(f"Streaming execution of node {node_id} ({node_data.node_type})")
            
            # Get node implementation
            if node_data.node_type not in self.node_registry:
                raise ExecutionError(f"Unknown node type: {node_data.node_type}")
                
            node_class = self.node_registry[node_data.node_type]
            node_instance = node_class()
            
            # Collect inputs from connected nodes
            inputs = {}
            for port in node_instance.spec.inputs:
                value = context.get_input_value(node_id, port.name)
                if value is not None:
                    inputs[port.name] = value
                elif port.required and port.default is not None:
                    inputs[port.name] = port.default
                    
            # Validate inputs
            if not node_instance.validate_inputs(inputs):
                missing_required = [p.name for p in node_instance.spec.inputs if p.required and p.name not in inputs]
                raise ExecutionError(f"Invalid inputs for node {node_id}. Missing required inputs: {missing_required}")
                
            # Check if node has streaming execution method
            if hasattr(node_instance, 'execute_streaming'):
                # Use streaming execution
                final_outputs = {}
                async for chunk in node_instance.execute_streaming(context, node_data):
                    yield chunk
                    
                # Get final outputs after streaming
                final_outputs = await node_instance.execute(context, node_data)
                
                # Store outputs
                for port_name, value in final_outputs.items():
                    context.set_node_output(node_id, port_name, value)
                    
            else:
                # Fall back to regular execution for non-streaming nodes
                outputs = await node_instance.execute(context, node_data)
                
                # Store outputs
                for port_name, value in outputs.items():
                    context.set_node_output(node_id, port_name, value)
                    
                # Yield the complete output as a single chunk
                if "response" in outputs:
                    yield outputs["response"]
                    
            context.set_node_status(node_id, NodeStatus.COMPLETED)
            logger.debug(f"Node {node_id} streaming completed successfully")
            
        except Exception as e:
            context.set_node_status(node_id, NodeStatus.FAILED)
            context.errors[node_id] = str(e)
            logger.error(f"Node {node_id} streaming failed: {e}")
            raise ExecutionError(f"Node {node_id} streaming failed: {e}") from e


class NodeRegistry:
    """Registry for node types."""
    
    def __init__(self):
        self._nodes: Dict[str, type[BaseNode]] = {}
        
    def register(self, node_type: str, node_class: type[BaseNode]) -> None:
        """Register a node type."""
        self._nodes[node_type] = node_class
        logger.info(f"Registered node type: {node_type}")
        
    def get(self, node_type: str) -> Optional[type[BaseNode]]:
        """Get a node class by type."""
        return self._nodes.get(node_type)
        
    def list_types(self) -> List[str]:
        """Get list of registered node types."""
        return list(self._nodes.keys())
        
    def get_all(self) -> Dict[str, type[BaseNode]]:
        """Get all registered nodes."""
        return self._nodes.copy()