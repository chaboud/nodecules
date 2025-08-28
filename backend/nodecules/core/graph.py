"""Graph validation and topological sorting."""

from collections import defaultdict, deque
from typing import Dict, List, Set, Tuple

from .types import EdgeData, GraphData, NodeData


class GraphValidationError(Exception):
    """Raised when graph validation fails."""
    pass


class GraphValidator:
    """Validates graph structure and dependencies."""
    
    def __init__(self, graph: GraphData):
        self.graph = graph
        
    def validate(self) -> Tuple[bool, List[str]]:
        """Validate the graph and return (is_valid, errors)."""
        errors = []
        
        # Check for cycles
        try:
            self._topological_sort()
        except GraphValidationError as e:
            errors.append(str(e))
            
        # Check for orphaned edges
        node_ids = set(self.graph.nodes.keys())
        for edge in self.graph.edges:
            if edge.source_node not in node_ids:
                errors.append(f"Edge references non-existent source node: {edge.source_node}")
            if edge.target_node not in node_ids:
                errors.append(f"Edge references non-existent target node: {edge.target_node}")
                
        # Check for duplicate edges
        edge_signatures = set()
        for edge in self.graph.edges:
            signature = (edge.source_node, edge.source_port, edge.target_node, edge.target_port)
            if signature in edge_signatures:
                errors.append(f"Duplicate edge: {edge.source_node}.{edge.source_port} -> {edge.target_node}.{edge.target_port}")
            edge_signatures.add(signature)
            
        return len(errors) == 0, errors
        
    def _topological_sort(self) -> List[str]:
        """Internal topological sort for cycle detection."""
        # Build adjacency graph
        graph = defaultdict(list)
        in_degree = defaultdict(int)
        
        # Initialize all nodes with in_degree 0
        for node_id in self.graph.nodes:
            in_degree[node_id] = 0
            
        # Build graph and calculate in-degrees
        for edge in self.graph.edges:
            graph[edge.source_node].append(edge.target_node)
            in_degree[edge.target_node] += 1
            
        # Kahn's algorithm
        queue = deque([node for node, degree in in_degree.items() if degree == 0])
        result = []
        
        while queue:
            node = queue.popleft()
            result.append(node)
            
            # Reduce in-degree of neighbors
            for neighbor in graph[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)
                    
        # Check for cycles
        if len(result) != len(self.graph.nodes):
            raise GraphValidationError("Graph contains cycles")
            
        return result


class GraphExecutionPlanner:
    """Plans graph execution order and parallelization."""
    
    def __init__(self, graph: GraphData):
        self.graph = graph
        self.validator = GraphValidator(graph)
        
    def get_execution_order(self) -> List[str]:
        """Get topologically sorted execution order."""
        is_valid, errors = self.validator.validate()
        if not is_valid:
            raise GraphValidationError(f"Invalid graph: {', '.join(errors)}")
            
        return self._topological_sort()
        
    def get_parallel_batches(self) -> List[List[str]]:
        """Get nodes grouped into parallel execution batches."""
        # Build dependency graph
        dependencies = self._build_dependency_graph()
        
        # Group nodes by dependency level
        batches = []
        processed = set()
        
        while len(processed) < len(self.graph.nodes):
            # Find nodes with no remaining dependencies
            ready_nodes = []
            for node_id in self.graph.nodes:
                if node_id not in processed:
                    deps = dependencies.get(node_id, set())
                    if deps.issubset(processed):
                        ready_nodes.append(node_id)
                        
            if not ready_nodes:
                raise GraphValidationError("Cannot resolve dependencies - possible cycle")
                
            batches.append(ready_nodes)
            processed.update(ready_nodes)
            
        return batches
        
    def _build_dependency_graph(self) -> Dict[str, Set[str]]:
        """Build reverse dependency graph (node -> its dependencies)."""
        dependencies = defaultdict(set)
        
        for edge in self.graph.edges:
            dependencies[edge.target_node].add(edge.source_node)
            
        return dict(dependencies)
        
    def _topological_sort(self) -> List[str]:
        """Topological sort using Kahn's algorithm."""
        # Build adjacency graph
        graph = defaultdict(list)
        in_degree = defaultdict(int)
        
        # Initialize all nodes with in_degree 0
        for node_id in self.graph.nodes:
            in_degree[node_id] = 0
            
        # Build graph and calculate in-degrees
        for edge in self.graph.edges:
            graph[edge.source_node].append(edge.target_node)
            in_degree[edge.target_node] += 1
            
        # Kahn's algorithm
        queue = deque([node for node, degree in in_degree.items() if degree == 0])
        result = []
        
        while queue:
            node = queue.popleft()
            result.append(node)
            
            # Reduce in-degree of neighbors
            for neighbor in graph[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)
                    
        return result