# Plugin System Architecture

## Overview

The Plugin System provides a flexible, secure framework for extending the pipeline with custom nodes, processors, and integrations. It supports hot-reloading in development, sandboxed execution for security, and seamless integration with the visual editor.

## Plugin Architecture

### Core Concepts

```python
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
import inspect

@dataclass
class PluginManifest:
    """Plugin metadata and configuration"""
    name: str
    version: str
    author: str
    description: str
    license: str
    dependencies: List[str]
    node_types: List[str]
    permissions: List[str]
    entry_point: str
    min_engine_version: str
    max_engine_version: Optional[str]
    
@dataclass
class NodeSpecification:
    """Node type specification for visual editor"""
    node_type: str
    display_name: str
    category: str
    description: str
    icon: Optional[str]
    color: Optional[str]
    inputs: List[PortSpecification]
    outputs: List[PortSpecification]
    parameters: List[ParameterSpecification]
    resource_requirements: ResourceRequirement
    
@dataclass
class PortSpecification:
    """Input/output port specification"""
    name: str
    type: str
    display_name: str
    description: str
    required: bool = True
    default: Any = None
    multiple: bool = False
    
@dataclass
class ParameterSpecification:
    """Node parameter specification"""
    name: str
    type: str
    display_name: str
    description: str
    default: Any
    constraints: Optional[Dict[str, Any]] = None
    ui_component: str = "input"  # input, select, slider, checkbox
```

### Base Plugin Interface

```python
class BasePlugin(ABC):
    """Abstract base class for all plugins"""
    
    def __init__(self, manifest: PluginManifest):
        self.manifest = manifest
        self.logger = self.get_logger()
        self.initialized = False
        
    @abstractmethod
    def initialize(self, context: PluginContext) -> None:
        """Initialize plugin with system context"""
        pass
        
    @abstractmethod
    def shutdown(self) -> None:
        """Clean shutdown of plugin resources"""
        pass
        
    @abstractmethod
    def get_node_types(self) -> List[NodeSpecification]:
        """Return list of node types provided by plugin"""
        pass
        
    @abstractmethod
    def create_node(self, node_type: str, parameters: Dict) -> 'BaseNode':
        """Factory method to create node instances"""
        pass
        
    def validate_permissions(self, required: List[str]) -> bool:
        """Check if plugin has required permissions"""
        return all(perm in self.manifest.permissions for perm in required)
        
    def get_logger(self):
        """Get plugin-specific logger"""
        import logging
        return logging.getLogger(f"plugin.{self.manifest.name}")
```

### Node Plugin Interface

```python
class BaseNode(ABC):
    """Abstract base class for plugin nodes"""
    
    def __init__(self, node_id: str, parameters: Dict[str, Any]):
        self.node_id = node_id
        self.parameters = parameters
        self.inputs = {}
        self.outputs = {}
        
    @abstractmethod
    def validate_inputs(self, inputs: Dict[str, Any]) -> bool:
        """Validate input data before execution"""
        pass
        
    @abstractmethod
    def execute(self, inputs: Dict[str, Any], context: ExecutionContext) -> Dict[str, Any]:
        """Execute node logic and return outputs"""
        pass
        
    @abstractmethod
    def estimate_resources(self, inputs: Dict[str, Any]) -> ResourceRequirement:
        """Estimate resource requirements for execution"""
        pass
        
    def on_error(self, error: Exception, context: ExecutionContext) -> Optional[Dict[str, Any]]:
        """Handle execution errors, optionally return fallback output"""
        return None
        
    def cache_key(self, inputs: Dict[str, Any]) -> str:
        """Generate cache key for node execution"""
        import hashlib
        import json
        
        key_data = {
            'node_type': self.__class__.__name__,
            'parameters': self.parameters,
            'inputs': {k: str(v) for k, v in inputs.items()}
        }
        
        return hashlib.sha256(
            json.dumps(key_data, sort_keys=True).encode()
        ).hexdigest()
```

## Plugin Loading System

### Plugin Discovery

```python
class PluginDiscovery:
    """Discover and catalog available plugins"""
    
    def __init__(self, plugin_dirs: List[str]):
        self.plugin_dirs = plugin_dirs
        self.discovered_plugins = {}
        
    def scan_plugins(self) -> Dict[str, PluginManifest]:
        """Scan directories for plugin manifests"""
        plugins = {}
        
        for plugin_dir in self.plugin_dirs:
            for entry in os.scandir(plugin_dir):
                if entry.is_dir():
                    manifest_path = os.path.join(entry.path, 'plugin.yaml')
                    if os.path.exists(manifest_path):
                        manifest = self.load_manifest(manifest_path)
                        plugins[manifest.name] = manifest
                        
        return plugins
        
    def load_manifest(self, path: str) -> PluginManifest:
        """Load and validate plugin manifest"""
        import yaml
        
        with open(path, 'r') as f:
            data = yaml.safe_load(f)
            
        # Validate required fields
        required_fields = ['name', 'version', 'author', 'entry_point']
        for field in required_fields:
            if field not in data:
                raise ValueError(f"Missing required field: {field}")
                
        return PluginManifest(**data)
```

### Dynamic Loading

```python
class PluginLoader:
    """Dynamic plugin loading with isolation"""
    
    def __init__(self, discovery: PluginDiscovery):
        self.discovery = discovery
        self.loaded_plugins = {}
        self.sandbox_manager = SandboxManager()
        
    def load_plugin(self, plugin_name: str, sandbox: bool = True) -> BasePlugin:
        """Load plugin with optional sandboxing"""
        
        if plugin_name in self.loaded_plugins:
            return self.loaded_plugins[plugin_name]
            
        manifest = self.discovery.discovered_plugins[plugin_name]
        
        # Check dependencies
        self.check_dependencies(manifest.dependencies)
        
        # Load plugin module
        if sandbox:
            plugin = self.load_sandboxed(manifest)
        else:
            plugin = self.load_direct(manifest)
            
        # Initialize plugin
        context = PluginContext(
            engine_version=self.get_engine_version(),
            available_resources=self.get_resources(),
            config=self.get_plugin_config(plugin_name)
        )
        plugin.initialize(context)
        
        self.loaded_plugins[plugin_name] = plugin
        return plugin
        
    def load_direct(self, manifest: PluginManifest) -> BasePlugin:
        """Load plugin directly (development mode)"""
        import importlib.util
        
        spec = importlib.util.spec_from_file_location(
            manifest.name,
            manifest.entry_point
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        
        # Find plugin class
        for name, obj in inspect.getmembers(module):
            if inspect.isclass(obj) and issubclass(obj, BasePlugin) and obj != BasePlugin:
                return obj(manifest)
                
        raise ValueError(f"No valid plugin class found in {manifest.entry_point}")
        
    def load_sandboxed(self, manifest: PluginManifest) -> BasePlugin:
        """Load plugin in sandboxed environment"""
        return self.sandbox_manager.load_plugin(manifest)

### Hot Reloading

```python
class HotReloadManager:
    """Manage plugin hot reloading for development"""
    
    def __init__(self, loader: PluginLoader):
        self.loader = loader
        self.file_watchers = {}
        self.reload_callbacks = []
        
    def watch_plugin(self, plugin_name: str):
        """Watch plugin files for changes"""
        import watchdog.observers
        import watchdog.events
        
        manifest = self.loader.discovery.discovered_plugins[plugin_name]
        plugin_dir = os.path.dirname(manifest.entry_point)
        
        class PluginChangeHandler(watchdog.events.FileSystemEventHandler):
            def __init__(self, manager, plugin_name):
                self.manager = manager
                self.plugin_name = plugin_name
                
            def on_modified(self, event):
                if event.src_path.endswith('.py'):
                    self.manager.reload_plugin(self.plugin_name)
                    
        handler = PluginChangeHandler(self, plugin_name)
        observer = watchdog.observers.Observer()
        observer.schedule(handler, plugin_dir, recursive=True)
        observer.start()
        
        self.file_watchers[plugin_name] = observer
        
    def reload_plugin(self, plugin_name: str):
        """Reload plugin and notify listeners"""
        self.loader.unload_plugin(plugin_name)
        plugin = self.loader.load_plugin(plugin_name, sandbox=False)
        
        for callback in self.reload_callbacks:
            callback(plugin_name, plugin)
            
        return plugin
```

## Security and Sandboxing

### Permission System

```python
class PermissionManager:
    """Manage plugin permissions"""
    
    PERMISSIONS = {
        'filesystem.read': 'Read access to filesystem',
        'filesystem.write': 'Write access to filesystem',
        'network.http': 'HTTP network access',
        'network.socket': 'Raw socket access',
        'system.exec': 'Execute system commands',
        'gpu.access': 'GPU access',
        'memory.unlimited': 'Unlimited memory usage'
    }
    
    def __init__(self):
        self.granted_permissions = {}
        
    def request_permissions(self, plugin_name: str, 
                           requested: List[str]) -> bool:
        """Request permissions for plugin"""
        # Check if permissions are valid
        for perm in requested:
            if perm not in self.PERMISSIONS:
                raise ValueError(f"Invalid permission: {perm}")
                
        # In production, this would prompt user or check policy
        # For now, auto-grant read permissions, deny others
        safe_permissions = ['filesystem.read', 'network.http']
        
        granted = [p for p in requested if p in safe_permissions]
        self.granted_permissions[plugin_name] = granted
        
        return len(granted) == len(requested)
        
    def check_permission(self, plugin_name: str, permission: str) -> bool:
        """Check if plugin has specific permission"""
        return permission in self.granted_permissions.get(plugin_name, [])
```

### Sandbox Implementation

```python
class SandboxManager:
    """Manage sandboxed plugin execution"""
    
    def __init__(self):
        self.sandboxes = {}
        
    def load_plugin(self, manifest: PluginManifest) -> BasePlugin:
        """Load plugin in isolated sandbox"""
        
        # Option 1: Process isolation
        if manifest.name not in self.sandboxes:
            self.sandboxes[manifest.name] = ProcessSandbox(manifest)
            
        return self.sandboxes[manifest.name].load()
        
class ProcessSandbox:
    """Process-based plugin isolation"""
    
    def __init__(self, manifest: PluginManifest):
        self.manifest = manifest
        self.process = None
        self.rpc_client = None
        
    def load(self) -> BasePlugin:
        """Start isolated process and load plugin"""
        import multiprocessing
        from multiprocessing.connection import Client
        
        # Start sandbox process
        parent_conn, child_conn = multiprocessing.Pipe()
        self.process = multiprocessing.Process(
            target=self._sandbox_main,
            args=(self.manifest, child_conn)
        )
        self.process.start()
        
        # Create RPC proxy
        return PluginProxy(parent_conn, self.manifest)
        
    def _sandbox_main(self, manifest: PluginManifest, conn):
        """Main function for sandboxed process"""
        # Set resource limits
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, -1))  # 512MB memory
        
        # Load plugin in restricted environment
        plugin = self._load_restricted(manifest)
        
        # Handle RPC calls
        while True:
            try:
                method, args, kwargs = conn.recv()
                result = getattr(plugin, method)(*args, **kwargs)
                conn.send(('success', result))
            except Exception as e:
                conn.send(('error', str(e)))
```

## Plugin Development

### Plugin Template

```python
# example_plugin.py
from pipeline.plugin import BasePlugin, BaseNode, NodeSpecification

class ExamplePlugin(BasePlugin):
    """Example plugin implementation"""
    
    def initialize(self, context):
        """Initialize plugin resources"""
        self.initialized = True
        self.logger.info(f"Initialized {self.manifest.name} plugin")
        
    def shutdown(self):
        """Clean up plugin resources"""
        self.initialized = False
        
    def get_node_types(self):
        """Return available node types"""
        return [
            NodeSpecification(
                node_type="example_processor",
                display_name="Example Processor",
                category="Processing",
                description="Processes data in an example way",
                inputs=[
                    PortSpecification(
                        name="input",
                        type="any",
                        display_name="Input Data",
                        description="Data to process"
                    )
                ],
                outputs=[
                    PortSpecification(
                        name="output",
                        type="any",
                        display_name="Processed Data",
                        description="Processed result"
                    )
                ],
                parameters=[
                    ParameterSpecification(
                        name="mode",
                        type="string",
                        display_name="Processing Mode",
                        description="How to process the data",
                        default="standard",
                        constraints={"enum": ["standard", "advanced", "minimal"]},
                        ui_component="select"
                    )
                ]
            )
        ]
        
    def create_node(self, node_type, parameters):
        """Create node instance"""
        if node_type == "example_processor":
            return ExampleProcessorNode(parameters)
        raise ValueError(f"Unknown node type: {node_type}")

class ExampleProcessorNode(BaseNode):
    """Example node implementation"""
    
    def validate_inputs(self, inputs):
        """Validate inputs before processing"""
        return "input" in inputs and inputs["input"] is not None
        
    def execute(self, inputs, context):
        """Execute node processing"""
        data = inputs["input"]
        mode = self.parameters.get("mode", "standard")
        
        # Process based on mode
        if mode == "standard":
            result = self._standard_processing(data)
        elif mode == "advanced":
            result = self._advanced_processing(data)
        else:
            result = self._minimal_processing(data)
            
        return {"output": result}
        
    def estimate_resources(self, inputs):
        """Estimate required resources"""
        return ResourceRequirement(
            cpu_cores=1.0,
            memory_mb=256,
            gpu_count=0
        )
```

### Plugin Manifest

```yaml
# plugin.yaml
name: example_plugin
version: 1.0.0
author: Your Name
description: Example plugin for demonstration
license: MIT

dependencies:
  - numpy>=1.20.0
  - pandas>=1.3.0

node_types:
  - example_processor
  - example_transformer

permissions:
  - filesystem.read
  - network.http

entry_point: ./example_plugin.py
min_engine_version: 1.0.0
max_engine_version: 2.0.0

configuration:
  api_key:
    type: string
    description: API key for external service
    required: false
  max_batch_size:
    type: integer
    description: Maximum batch size for processing
    default: 100
```

## Plugin Registry

### Central Registry

```python
class PluginRegistry:
    """Central registry for managing plugins"""
    
    def __init__(self, db_connection: str):
        self.db = create_engine(db_connection)
        self.plugins = {}
        self.node_types = {}
        
    def register_plugin(self, plugin: BasePlugin):
        """Register plugin in central registry"""
        manifest = plugin.manifest
        
        # Store in database
        with self.db.begin() as conn:
            conn.execute(
                """
                INSERT INTO plugins (name, version, author, metadata)
                VALUES (:name, :version, :author, :metadata)
                ON CONFLICT (name) DO UPDATE
                SET version = :version, metadata = :metadata
                """,
                {
                    "name": manifest.name,
                    "version": manifest.version,
                    "author": manifest.author,
                    "metadata": json.dumps(manifest.dict())
                }
            )
            
        # Register node types
        for node_spec in plugin.get_node_types():
            self.node_types[node_spec.node_type] = {
                "plugin": manifest.name,
                "specification": node_spec
            }
            
        self.plugins[manifest.name] = plugin
        
    def get_available_nodes(self, category: str = None) -> List[NodeSpecification]:
        """Get all available node types, optionally filtered by category"""
        nodes = []
        
        for node_info in self.node_types.values():
            spec = node_info["specification"]
            if category is None or spec.category == category:
                nodes.append(spec)
                
        return nodes
```

## Visual Editor Integration

### Node Palette

```typescript
// NodePalette.tsx
import React from 'react';
import { useDrag } from 'react-dnd';

interface NodeType {
  nodeType: string;
  displayName: string;
  category: string;
  icon?: string;
  color?: string;
}

const NodePaletteItem: React.FC<{ nodeType: NodeType }> = ({ nodeType }) => {
  const [{ isDragging }, drag] = useDrag({
    type: 'node',
    item: { type: nodeType.nodeType },
    collect: (monitor) => ({
      isDragging: monitor.isDragging(),
    }),
  });

  return (
    <div
      ref={drag}
      className={`node-palette-item ${isDragging ? 'dragging' : ''}`}
      style={{ backgroundColor: nodeType.color || '#666' }}
    >
      {nodeType.icon && <span className="icon">{nodeType.icon}</span>}
      <span className="name">{nodeType.displayName}</span>
    </div>
  );
};

const NodePalette: React.FC = () => {
  const [nodeTypes, setNodeTypes] = React.useState<NodeType[]>([]);
  const [categories, setCategories] = React.useState<string[]>([]);

  React.useEffect(() => {
    // Fetch available node types from backend
    fetch('/api/plugins/nodes')
      .then(res => res.json())
      .then(data => {
        setNodeTypes(data.nodes);
        setCategories([...new Set(data.nodes.map(n => n.category))]);
      });
  }, []);

  return (
    <div className="node-palette">
      {categories.map(category => (
        <div key={category} className="category">
          <h3>{category}</h3>
          {nodeTypes
            .filter(n => n.category === category)
            .map(nodeType => (
              <NodePaletteItem key={nodeType.nodeType} nodeType={nodeType} />
            ))}
        </div>
      ))}
    </div>
  );
};
```

### Dynamic Node Rendering

```typescript
// DynamicNode.tsx
import React from 'react';
import { Handle, Position } from 'reactflow';

interface DynamicNodeProps {
  data: {
    nodeType: string;
    specification: any;
    parameters: Record<string, any>;
    onParameterChange: (name: string, value: any) => void;
  };
}

const DynamicNode: React.FC<DynamicNodeProps> = ({ data }) => {
  const { specification, parameters, onParameterChange } = data;

  const renderParameter = (param: any) => {
    switch (param.ui_component) {
      case 'slider':
        return (
          <input
            type="range"
            min={param.constraints?.min || 0}
            max={param.constraints?.max || 100}
            value={parameters[param.name] || param.default}
            onChange={(e) => onParameterChange(param.name, e.target.value)}
          />
        );
      case 'select':
        return (
          <select
            value={parameters[param.name] || param.default}
            onChange={(e) => onParameterChange(param.name, e.target.value)}
          >
            {param.constraints?.enum?.map((option: string) => (
              <option key={option} value={option}>{option}</option>
            ))}
          </select>
        );
      default:
        return (
          <input
            type="text"
            value={parameters[param.name] || param.default}
            onChange={(e) => onParameterChange(param.name, e.target.value)}
          />
        );
    }
  };

  return (
    <div className="dynamic-node" style={{ backgroundColor: specification.color }}>
      <div className="node-header">{specification.display_name}</div>
      
      {specification.inputs.map((input: any) => (
        <Handle
          key={input.name}
          type="target"
          position={Position.Left}
          id={input.name}
          style={{ top: `${20 + specification.inputs.indexOf(input) * 30}px` }}
        />
      ))}

      <div className="node-parameters">
        {specification.parameters.map((param: any) => (
          <div key={param.name} className="parameter">
            <label>{param.display_name}</label>
            {renderParameter(param)}
          </div>
        ))}
      </div>

      {specification.outputs.map((output: any) => (
        <Handle
          key={output.name}
          type="source"
          position={Position.Right}
          id={output.name}
          style={{ top: `${20 + specification.outputs.indexOf(output) * 30}px` }}
        />
      ))}
    </div>
  );
};
```

## Testing Framework

### Plugin Testing

```python
import pytest
from unittest.mock import Mock, patch

class TestPluginFramework:
    """Test framework for plugin development"""
    
    @pytest.fixture
    def plugin_context(self):
        """Create mock plugin context"""
        return PluginContext(
            engine_version="1.0.0",
            available_resources=ResourcePool(
                available_cpu=8.0,
                available_memory=16384
            ),
            config={}
        )
        
    @pytest.fixture
    def test_plugin(self, plugin_context):
        """Create test plugin instance"""
        manifest = PluginManifest(
            name="test_plugin",
            version="1.0.0",
            author="Test",
            description="Test plugin",
            entry_point="test.py"
        )
        plugin = ExamplePlugin(manifest)
        plugin.initialize(plugin_context)
        return plugin
        
    def test_plugin_initialization(self, test_plugin):
        """Test plugin initializes correctly"""
        assert test_plugin.initialized
        assert len(test_plugin.get_node_types()) > 0
        
    def test_node_creation(self, test_plugin):
        """Test node creation from plugin"""
        node = test_plugin.create_node("example_processor", {"mode": "standard"})
        assert isinstance(node, BaseNode)
        
    def test_node_execution(self, test_plugin):
        """Test node execution"""
        node = test_plugin.create_node("example_processor", {"mode": "standard"})
        inputs = {"input": "test_data"}
        context = Mock()
        
        outputs = node.execute(inputs, context)
        assert "output" in outputs
        
    def test_permission_validation(self, test_plugin):
        """Test permission checking"""
        assert test_plugin.validate_permissions(["filesystem.read"])
        assert not test_plugin.validate_permissions(["system.exec"])
```

## Plugin Distribution

### Package Structure

```
my_plugin/
├── plugin.yaml           # Plugin manifest
├── requirements.txt      # Python dependencies
├── src/
│   ├── __init__.py
│   ├── plugin.py        # Main plugin class
│   └── nodes/           # Node implementations
│       ├── __init__.py
│       └── processors.py
├── tests/
│   └── test_plugin.py
├── docs/
│   └── README.md
└── examples/
    └── example_workflow.json
```

### Distribution Methods

```python
class PluginPackager:
    """Package plugins for distribution"""
    
    def package_plugin(self, plugin_path: str, output_path: str):
        """Create distributable plugin package"""
        import zipfile
        import tempfile
        
        # Validate plugin structure
        self.validate_structure(plugin_path)
        
        # Create plugin package
        with zipfile.ZipFile(output_path, 'w') as zf:
            for root, dirs, files in os.walk(plugin_path):
                for file in files:
                    if not file.endswith('.pyc'):
                        file_path = os.path.join(root, file)
                        arc_path = os.path.relpath(file_path, plugin_path)
                        zf.write(file_path, arc_path)
                        
        # Generate checksum
        checksum = self.calculate_checksum(output_path)
        
        return {
            "package": output_path,
            "checksum": checksum,
            "metadata": self.extract_metadata(plugin_path)
        }
        
class PluginInstaller:
    """Install plugins from packages"""
    
    def install_from_package(self, package_path: str, 
                            plugins_dir: str = "./plugins"):
        """Install plugin from package file"""
        import zipfile
        
        # Verify package integrity
        if not self.verify_package(package_path):
            raise ValueError("Package verification failed")
            
        # Extract to plugins directory
        with zipfile.ZipFile(package_path, 'r') as zf:
            manifest = yaml.safe_load(zf.read('plugin.yaml'))
            plugin_dir = os.path.join(plugins_dir, manifest['name'])
            zf.extractall(plugin_dir)
            
        # Install dependencies
        self.install_dependencies(plugin_dir)
        
        return plugin_dir
```

## Configuration

```yaml
# plugin_system.yaml
plugins:
  directories:
    - ./plugins
    - ~/.pipeline/plugins
    - /usr/share/pipeline/plugins
    
  security:
    enable_sandboxing: true
    sandbox_type: process  # process, container, wasm
    default_permissions:
      - filesystem.read
      
  development:
    hot_reload: true
    watch_interval: 1.0
    debug_mode: true
    
  registry:
    enable: true
    url: http://localhost:5000/api/registry
    
  limits:
    max_memory_mb: 512
    max_cpu_percent: 50
    execution_timeout: 60
```

## Next Steps

1. Review [API Gateway & Service Layer](api-gateway-doc)
2. Implement [Node Development Guide](node-development-guide)
3. Configure [Security and Sandboxing](security-doc)
4. Set up [Plugin Testing Framework](plugin-testing-guide) 