"""Test harness bootstrap.

Puts `backend/` on `sys.path` so tests can import `nodecules.*` without the
package being installed. Also enables pytest-asyncio auto mode so async test
functions don't need a per-test decorator.
"""

from __future__ import annotations

import sys
from pathlib import Path

# backend/ is the parent of tests/; put it at the front of sys.path.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))
