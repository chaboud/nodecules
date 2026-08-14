"""Service-backed nodes — chat, context, graph-as-node.

These node families depend on the chat-context subsystem
(`smart_context.py`, `content_addressable_context.py`), which in turn
requires a live Postgres and Redis. Import this module only from the
FastAPI application startup path, never from `core/` or other
import-at-library-load-time positions.

See CLAUDE.md invariant #4 ("Core library works without Postgres/Redis")
and TODO.md "Quarantine chat-context subsystem out of `core/`".
"""

from __future__ import annotations

from typing import Dict, Type

from ..core.types import BaseNode


def load_service_nodes() -> Dict[str, Type[BaseNode]]:
    """Import and aggregate every service-backed node registry.

    Lazy-imports its dependencies so static library consumers (the
    scheduler, tests, stenota) don't drag Postgres / Redis / anthropic /
    boto3 along for the ride. Call once during app startup.
    """
    from .smart_chat_node import SMART_CHAT_NODES
    from .immutable_chat_node import IMMUTABLE_CHAT_NODES
    from .context_nodes import CONTEXT_NODES
    from .graph_nodes import GRAPH_NODES

    registry: Dict[str, Type[BaseNode]] = {}
    registry.update(SMART_CHAT_NODES)
    registry.update(IMMUTABLE_CHAT_NODES)
    registry.update(CONTEXT_NODES)
    registry.update(GRAPH_NODES)
    return registry


__all__ = ["load_service_nodes"]
