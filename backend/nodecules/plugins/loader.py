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
            
    def load_all_plugins(self) -> None:
        """Discover and load all plugins."""
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
        logger.info(f"Loaded {len(self.loader.loaded_plugins)} plugins with {len(self.loader.node_classes)} node types")
        
    def get_node_registry(self) -> Dict[str, Type[BaseNode]]:
        """Get the complete node registry for the execution engine."""
        return self.loader.node_classes.copy()
        
    def get_available_nodes(self) -> List[NodeSpec]:
        """Get specifications for all available node types."""
        return self.loader.get_node_specs()