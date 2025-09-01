"""Graph-as-node functionality for recursive graph execution."""

import json
from typing import Dict, Any, List, Optional

from ..core.types import NodeSpec, PortSpec, ParameterSpec, DataType, ExecutionContext, NodeData, GraphData, NodeData as GraphNodeData, EdgeData, BaseNode
from ..core.executor import GraphExecutor
from ..models.database import get_database
from ..models.schemas import Graph
from ..api.graphs import resolve_graph_by_id_or_name


class SubgraphNode(BaseNode):
    """Node that executes another graph as a sub-process."""
    
    NODE_TYPE = "subgraph"
    
    def __init__(self):
        spec = NodeSpec(
            node_type=self.NODE_TYPE,
            display_name="Subgraph",
            description="Execute another graph as a node with exposed inputs/outputs",
            category="Flow Control",
            inputs=[
                PortSpec(
                    name="trigger",
                    data_type=DataType.ANY,
                    required=False,
                    description="Optional trigger input"
                )
            ],
            outputs=[
                PortSpec(
                    name="result",
                    data_type=DataType.ANY,
                    description="Result from subgraph execution"
                ),
                PortSpec(
                    name="execution_info",
                    data_type=DataType.TEXT,
                    description="Information about subgraph execution"
                )
            ],
            parameters=[
                ParameterSpec(
                    name="graph_id",
                    data_type="string",
                    default="",
                    description="ID or name of the graph to execute"
                ),
                ParameterSpec(
                    name="input_mapping",
                    data_type="text", 
                    default="{}",
                    description="JSON mapping of node inputs to subgraph inputs"
                ),
                ParameterSpec(
                    name="output_mapping", 
                    data_type="text",
                    default="{}",
                    description="JSON mapping of subgraph outputs to node outputs"
                ),
                ParameterSpec(
                    name="isolation_mode",
                    data_type="select",
                    default="isolated",
                    description="Execution isolation level",
                    constraints={
                        "options": ["isolated", "shared_context", "inherit_context"]
                    }
                )
            ]
        )
        super().__init__(spec)
    
    async def execute(self, context: ExecutionContext, node_data: NodeData) -> Dict[str, Any]:
        """Execute the subgraph node."""
        # Get parameters
        params = node_data.parameters
        graph_id = params.get("graph_id", "")
        input_mapping_str = params.get("input_mapping", "{}")
        output_mapping_str = params.get("output_mapping", "{}")
        isolation_mode = params.get("isolation_mode", "isolated")
        
        if not graph_id:
            return {
                "result": None,
                "execution_info": "Error: No graph_id specified"
            }
        
        try:
            # Parse mappings
            input_mapping = json.loads(input_mapping_str)
            output_mapping = json.loads(output_mapping_str)
            
            # Get database session
            db = next(get_database())
            
            try:
                # Resolve the subgraph
                subgraph = resolve_graph_by_id_or_name(graph_id, db)
                
                # Convert to internal format
                nodes = {}
                for node_id, node_data_dict in subgraph.nodes.items():
                    nodes[node_id] = GraphNodeData(
                        node_id=node_data_dict["node_id"],
                        node_type=node_data_dict["node_type"],
                        position=node_data_dict.get("position", {}),
                        parameters=node_data_dict.get("parameters", {})
                    )
                
                edges = []
                for edge_data in subgraph.edges:
                    edges.append(EdgeData(
                        edge_id=edge_data["edge_id"],
                        source_node=edge_data["source_node"],
                        source_port=edge_data["source_port"],
                        target_node=edge_data["target_node"],
                        target_port=edge_data["target_port"]
                    ))
                
                graph_data = GraphData(
                    graph_id=str(subgraph.id),
                    name=subgraph.name,
                    nodes=nodes,
                    edges=edges,
                    meta_data=subgraph.meta_data
                )
                
                # Prepare inputs for subgraph
                subgraph_inputs = {}
                
                # Map inputs from current execution context
                for node_input, subgraph_input in input_mapping.items():
                    try:
                        input_value = context.get_input_value(node_data.node_id, node_input)
                        if input_value is not None:
                            subgraph_inputs[subgraph_input] = input_value
                    except Exception:
                        # Input not available, skip
                        pass
                
                # Add trigger input if available
                trigger_value = context.get_input_value(node_data.node_id, "trigger", default=None)
                if trigger_value is not None:
                    subgraph_inputs["_trigger"] = trigger_value
                
                # Create subgraph execution context based on isolation mode
                if isolation_mode == "inherit_context":
                    # Use same context (dangerous but powerful)
                    subgraph_context = context
                elif isolation_mode == "shared_context":
                    # Create new context but share node registry
                    from ..core.executor import ExecutionContext as SubContext
                    subgraph_context = SubContext(
                        graph=graph_data,
                        node_registry=context.node_registry,
                        execution_inputs=subgraph_inputs,
                        context_id=f"{context.context_id}:subgraph:{node_data.node_id}"
                    )
                else:  # isolated
                    # Completely isolated execution
                    from ..core.executor import ExecutionContext as SubContext
                    subgraph_context = SubContext(
                        graph=graph_data,
                        node_registry=context.node_registry,  # Share node types but not state
                        execution_inputs=subgraph_inputs,
                        context_id=f"subgraph:{node_data.node_id}:{subgraph.id}"
                    )
                
                # Execute the subgraph
                if isolation_mode == "inherit_context":
                    # Execute in same context (modify execution inputs temporarily)
                    original_inputs = context.execution_inputs.copy()
                    context.execution_inputs.update(subgraph_inputs)
                    
                    executor = GraphExecutor(context.node_registry)
                    subgraph_result = await executor.execute_graph(graph_data, subgraph_inputs)
                    
                    # Restore original inputs
                    context.execution_inputs = original_inputs
                else:
                    # Execute in separate context
                    executor = GraphExecutor(context.node_registry)
                    subgraph_result = await executor.execute_graph(graph_data, subgraph_inputs)
                
                # Map outputs based on output_mapping
                result = {}
                execution_info = {
                    "subgraph_id": str(subgraph.id),
                    "subgraph_name": subgraph.name,
                    "status": subgraph_result.status.value if hasattr(subgraph_result, 'status') else "completed",
                    "node_count": len(subgraph.nodes),
                    "isolation_mode": isolation_mode
                }
                
                if hasattr(subgraph_result, 'node_outputs') and subgraph_result.node_outputs:
                    # Map specific outputs
                    if output_mapping:
                        for node_output, mapped_name in output_mapping.items():
                            if node_output in subgraph_result.node_outputs:
                                result[mapped_name] = subgraph_result.node_outputs[node_output]
                    else:
                        # No mapping specified, return all outputs
                        result = subgraph_result.node_outputs
                    
                    execution_info["output_nodes"] = list(subgraph_result.node_outputs.keys())
                else:
                    result = {"status": "completed"}
                    execution_info["output_nodes"] = []
                
                # Add error information if any
                if hasattr(subgraph_result, 'errors') and subgraph_result.errors:
                    execution_info["errors"] = subgraph_result.errors
                
                return {
                    "result": result,
                    "execution_info": json.dumps(execution_info, indent=2)
                }
                
            finally:
                db.close()
                
        except Exception as e:
            return {
                "result": None,
                "execution_info": f"Error executing subgraph: {str(e)}"
            }


class DynamicGraphNode(BaseNode):
    """Node that can dynamically adapt its inputs/outputs based on a target graph."""
    
    NODE_TYPE = "dynamic_graph"
    
    def __init__(self, target_graph_schema: Dict[str, Any] = None):
        """Initialize with optional schema from target graph."""
        
        # Base inputs/outputs that are always present
        base_inputs = [
            PortSpec(
                name="execute",
                data_type=DataType.ANY,
                required=False,
                description="Trigger execution"
            )
        ]
        
        base_outputs = [
            PortSpec(
                name="status",
                data_type=DataType.TEXT,
                description="Execution status"
            )
        ]
        
        # Add dynamic ports based on schema
        if target_graph_schema:
            # Add input ports for each input node in the target graph
            for input_spec in target_graph_schema.get("inputs", []):
                base_inputs.append(PortSpec(
                    name=input_spec.get("ordinal_key", f"input_{len(base_inputs)}"),
                    data_type=DataType.TEXT,  # TODO: Map actual data types
                    required=False,
                    description=input_spec.get("description", "Dynamic input")
                ))
            
            # Add output ports for each output node in the target graph  
            for output_spec in target_graph_schema.get("outputs", []):
                base_outputs.append(PortSpec(
                    name=output_spec.get("label", f"output_{len(base_outputs)}").lower().replace(" ", "_"),
                    data_type=DataType.TEXT,  # TODO: Map actual data types
                    description=output_spec.get("description", "Dynamic output")
                ))
        
        spec = NodeSpec(
            node_type=self.NODE_TYPE,
            display_name="Dynamic Graph",
            description="Graph node with dynamically adapted inputs/outputs",
            category="Flow Control",
            inputs=base_inputs,
            outputs=base_outputs,
            parameters=[
                ParameterSpec(
                    name="target_graph",
                    data_type="string",
                    default="",
                    description="Target graph ID or name"
                ),
                ParameterSpec(
                    name="auto_map_inputs",
                    data_type="boolean",
                    default=True,
                    description="Automatically map inputs by name"
                ),
                ParameterSpec(
                    name="context_isolation",
                    data_type="select",
                    default="isolated",
                    description="Execution context isolation",
                    constraints={
                        "options": ["isolated", "shared", "inherited"]
                    }
                )
            ]
        )
        super().__init__(spec)
        self.target_graph_schema = target_graph_schema
    
    async def execute(self, context: ExecutionContext, node_data: NodeData) -> Dict[str, Any]:
        """Execute the dynamic graph node."""
        params = node_data.parameters
        target_graph = params.get("target_graph", "")
        auto_map_inputs = params.get("auto_map_inputs", True)
        context_isolation = params.get("context_isolation", "isolated")
        
        if not target_graph:
            return {"status": "Error: No target graph specified"}
        
        try:
            # Use the subgraph functionality but with dynamic mapping
            subgraph_node = SubgraphNode()
            
            # Build input mapping automatically if enabled
            input_mapping = {}
            if auto_map_inputs and self.target_graph_schema:
                for input_spec in self.target_graph_schema.get("inputs", []):
                    ordinal_key = input_spec.get("ordinal_key")
                    label = input_spec.get("label")
                    
                    # Map by ordinal key primarily
                    if ordinal_key:
                        input_mapping[ordinal_key] = ordinal_key
                    
                    # Also map by label if available
                    if label:
                        input_mapping[label.lower().replace(" ", "_")] = label
            
            # Build output mapping
            output_mapping = {}
            if self.target_graph_schema:
                for output_spec in self.target_graph_schema.get("outputs", []):
                    label = output_spec.get("label", "output")
                    node_id = output_spec.get("node_id")
                    
                    mapped_name = label.lower().replace(" ", "_")
                    output_mapping[node_id] = mapped_name
            
            # Create modified node data for subgraph execution
            subgraph_node_data = NodeData(
                node_id=node_data.node_id,
                node_type="subgraph",
                position=node_data.position,
                parameters={
                    "graph_id": target_graph,
                    "input_mapping": json.dumps(input_mapping),
                    "output_mapping": json.dumps(output_mapping),
                    "isolation_mode": context_isolation
                }
            )
            
            # Execute using subgraph node
            result = await subgraph_node.execute(context, subgraph_node_data)
            
            # Flatten the result for cleaner output
            subgraph_result = result.get("result", {})
            execution_info = result.get("execution_info", "{}")
            
            output = {"status": "completed"}
            
            if isinstance(subgraph_result, dict):
                output.update(subgraph_result)
            
            return output
            
        except Exception as e:
            return {"status": f"Error: {str(e)}"}

    @classmethod
    async def create_from_graph(cls, graph_id: str) -> "DynamicGraphNode":
        """Factory method to create a dynamic graph node from a target graph."""
        try:
            # Get database session
            db = next(get_database())
            
            try:
                # Get the graph schema
                from ..api.graphs import get_graph_schema  # Avoid circular import
                
                # This would need to be implemented as a function that returns schema
                # For now, we'll create a basic node
                return cls()
                
            finally:
                db.close()
                
        except Exception:
            # Fallback to basic node
            return cls()


# Registry of graph nodes  
GRAPH_NODES = {
    "subgraph": SubgraphNode,
    "dynamic_graph": DynamicGraphNode,
}