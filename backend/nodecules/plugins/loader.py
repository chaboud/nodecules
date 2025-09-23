"""Plugin loading and management system."""

import importlib.util
import inspect
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Type

import yaml

from ..core.types import BaseNode, NodeSpec


logger = logging.getLogger(__name__)


class PluginManifest:
    """Plugin metadata from plugin.yaml."""
    
    def __init__(self, data: dict):
        self.name: str = data["name"]
        self.version: str = data["version"]
        self.author: str = data.get("author", "Unknown")
        self.description: str = data.get("description", "")
        self.entry_point: str = data["entry_point"]
        self.node_types: List[str] = data.get("node_types", [])
        self.dependencies: List[str] = data.get("dependencies", [])
        

class PluginLoader:
    """Loads and manages plugins from directories."""
    
    def __init__(self, plugin_dirs: List[str]):
        self.plugin_dirs = plugin_dirs
        self.loaded_plugins: Dict[str, dict] = {}
        self.node_classes: Dict[str, Type[BaseNode]] = {}
        
    def discover_plugins(self) -> List[PluginManifest]:
        """Discover all plugins in plugin directories."""
        plugins = []
        
        for plugin_dir in self.plugin_dirs:
            if not os.path.exists(plugin_dir):
                logger.warning(f"Plugin directory does not exist: {plugin_dir}")
                continue
                
            for item in os.listdir(plugin_dir):
                item_path = os.path.join(plugin_dir, item)
                if os.path.isdir(item_path):
                    manifest_path = os.path.join(item_path, "plugin.yaml")
                    if os.path.exists(manifest_path):
                        try:
                            manifest = self._load_manifest(manifest_path, item_path)
                            plugins.append(manifest)
                        except Exception as e:
                            logger.error(f"Failed to load plugin manifest {manifest_path}: {e}")
                            
        return plugins
        
    def auto_discover_plugins(self) -> List[dict]:
        """Auto-discover Python files containing BaseNode subclasses without requiring manifests."""
        auto_plugins = []
        
        for plugin_dir in self.plugin_dirs:
            if not os.path.exists(plugin_dir):
                continue
                
            # Scan for Python files directly in plugin directories
            for root, dirs, files in os.walk(plugin_dir):
                for file in files:
                    if file.endswith('.py') and not file.startswith('_'):
                        file_path = os.path.join(root, file)
                        
                        # Skip if this is part of a YAML-based plugin
                        plugin_folder = os.path.dirname(file_path)
                        if os.path.exists(os.path.join(plugin_folder, "plugin.yaml")):
                            continue
                            
                        try:
                            # Try to load the module and check for BaseNode classes
                            plugin_info = self._inspect_python_file(file_path)
                            if plugin_info and plugin_info['node_classes']:
                                auto_plugins.append(plugin_info)
                        except Exception as e:
                            logger.debug(f"Could not auto-discover plugin from {file_path}: {e}")
                            
        return auto_plugins
        
    def load_plugin(self, manifest: PluginManifest, plugin_dir: str) -> None:
        """Load a single plugin."""
        try:
            # Resolve entry point path
            entry_path = os.path.join(plugin_dir, manifest.entry_point)
            if not os.path.exists(entry_path):
                raise FileNotFoundError(f"Entry point not found: {entry_path}")
                
            # Import the plugin module
            spec = importlib.util.spec_from_file_location(manifest.name, entry_path)
            if spec is None or spec.loader is None:
                raise ImportError(f"Failed to create module spec for {entry_path}")
                
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # Find node classes in the module
            node_classes = self._extract_node_classes(module)
            
            # Register the plugin
            self.loaded_plugins[manifest.name] = {
                "manifest": manifest,
                "module": module,
                "node_classes": node_classes,
                "plugin_dir": plugin_dir
            }
            
            # Register node classes
            for node_type, node_class in node_classes.items():
                self.node_classes[node_type] = node_class
                
            logger.info(f"Loaded plugin: {manifest.name} v{manifest.version} with {len(node_classes)} node types")
            
        except Exception as e:
            logger.error(f"Failed to load plugin {manifest.name}: {e}")
            raise
            
    def load_auto_discovered_plugin(self, plugin_info: dict) -> None:
        """Load an auto-discovered plugin."""
        try:
            # Register the plugin
            self.loaded_plugins[plugin_info['name']] = plugin_info
            
            # Register node classes
            for node_type, node_class in plugin_info['node_classes'].items():
                if node_type in self.node_classes:
                    logger.warning(f"Node type '{node_type}' from auto-discovered plugin '{plugin_info['name']}' conflicts with existing registration")
                else:
                    self.node_classes[node_type] = node_class
                    
            logger.info(f"Auto-discovered plugin: {plugin_info['display_name']} with {len(plugin_info['node_classes'])} node types")
            
        except Exception as e:
            logger.error(f"Failed to load auto-discovered plugin {plugin_info['name']}: {e}")
            raise
    
    def load_all_plugins(self) -> None:
        """Discover and load all plugins (both YAML-based and auto-discovered)."""
        # Load YAML-based plugins first
        plugins = self.discover_plugins()
        
        for manifest in plugins:
            plugin_dir = None
            # Find the plugin directory for this manifest
            for base_dir in self.plugin_dirs:
                potential_dir = os.path.join(base_dir, manifest.name)
                if os.path.exists(os.path.join(potential_dir, "plugin.yaml")):
                    plugin_dir = potential_dir
                    break
                    
            if plugin_dir:
                try:
                    self.load_plugin(manifest, plugin_dir)
                except Exception as e:
                    logger.error(f"Failed to load plugin {manifest.name}: {e}")
                    
        # Then load auto-discovered plugins
        auto_plugins = self.auto_discover_plugins()
        for plugin_info in auto_plugins:
            try:
                self.load_auto_discovered_plugin(plugin_info)
            except Exception as e:
                logger.error(f"Failed to load auto-discovered plugin {plugin_info['name']}: {e}")
                    
    def get_node_class(self, node_type: str) -> Optional[Type[BaseNode]]:
        """Get a node class by type."""
        return self.node_classes.get(node_type)
        
    def get_all_node_types(self) -> List[str]:
        """Get list of all available node types."""
        return list(self.node_classes.keys())
        
    def get_node_specs(self) -> List[NodeSpec]:
        """Get specifications for all loaded node types."""
        specs = []
        for node_type, node_class in self.node_classes.items():
            try:
                # Create temporary instance to get spec
                instance = node_class()
                specs.append(instance.spec)
            except Exception as e:
                logger.error(f"Failed to get spec for node type {node_type}: {e}")
                
        return specs
        
    def _load_manifest(self, manifest_path: str, plugin_dir: str) -> PluginManifest:
        """Load plugin manifest from YAML file."""
        with open(manifest_path, 'r') as f:
            data = yaml.safe_load(f)
            
        # Validate required fields
        required_fields = ["name", "version", "entry_point"]
        for field in required_fields:
            if field not in data:
                raise ValueError(f"Missing required field in manifest: {field}")
                
        return PluginManifest(data)
        
    def _extract_node_classes(self, module) -> Dict[str, Type[BaseNode]]:
        """Extract node classes from a plugin module."""
        node_classes = {}
        
        for name, obj in inspect.getmembers(module):
            if (inspect.isclass(obj) and 
                issubclass(obj, BaseNode) and 
                obj != BaseNode):
                
                # Get node type from class or use class name
                if hasattr(obj, 'NODE_TYPE'):
                    node_type = obj.NODE_TYPE
                else:
                    node_type = name.lower().replace('node', '')
                    
                node_classes[node_type] = obj
                
        return node_classes
        
    def _inspect_python_file(self, file_path: str) -> Optional[dict]:
        """Inspect a Python file to see if it contains BaseNode subclasses."""
        try:
            # Create a unique module name based on the file path
            module_name = f"auto_plugin_{Path(file_path).stem}_{hash(file_path) % 10000}"
            
            # Load the module
            spec = importlib.util.spec_from_file_location(module_name, file_path)
            if spec is None or spec.loader is None:
                return None
                
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # Extract node classes
            node_classes = self._extract_node_classes(module)
            
            if not node_classes:
                return None
                
            # Create plugin info for auto-discovered plugin
            file_name = Path(file_path).stem
            return {
                'name': f"auto_{file_name}",
                'file_path': file_path,
                'module': module,
                'node_classes': node_classes,
                'display_name': f"Auto-discovered: {file_name}",
                'description': f"Auto-discovered plugin from {file_path}",
                'is_auto_discovered': True
            }
            
        except Exception as e:
            # This is expected for files that don't contain valid node classes
            logger.debug(f"Failed to inspect {file_path}: {e}")
            return None


class PluginManager:
    """High-level plugin management."""
    
    def __init__(self, plugin_dirs: Optional[List[str]] = None):
        if plugin_dirs is None:
            plugin_dirs = ["./plugins"]
            
        self.loader = PluginLoader(plugin_dirs)
        
    def initialize(self) -> None:
        """Initialize the plugin system."""
        logger.info("Initializing plugin system...")
        self.loader.load_all_plugins()
        
        # Count different types of plugins
        yaml_plugins = sum(1 for p in self.loader.loaded_plugins.values() if not p.get('is_auto_discovered', False))
        auto_plugins = sum(1 for p in self.loader.loaded_plugins.values() if p.get('is_auto_discovered', False))
        
        logger.info(f"Loaded {len(self.loader.loaded_plugins)} plugins ({yaml_plugins} YAML-based, {auto_plugins} auto-discovered) with {len(self.loader.node_classes)} node types")
        
    def get_node_registry(self) -> Dict[str, Type[BaseNode]]:
        """Get the complete node registry for the execution engine."""
        return self.loader.node_classes.copy()
        
    def get_available_nodes(self) -> List[NodeSpec]:
        """Get specifications for all available node types."""
        return self.loader.get_node_specs()