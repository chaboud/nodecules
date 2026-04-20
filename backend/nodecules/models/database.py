"""Database setup and configuration.

Engine and session factory are created lazily on first use so that
importing `nodecules.models.*` does not require a live Postgres. This
matches CLAUDE.md invariant #4: the core library must be usable against
only the filesystem. Full FastAPI paths call `get_database()` which
triggers the real bind.
"""

from __future__ import annotations

import os
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, sessionmaker


# Database URL from environment. Evaluated lazily in `_get_engine()` so
# tests / library consumers that never hit the DB don't care what this
# string is.
def _database_url() -> str:
    return os.getenv(
        "DATABASE_URL",
        "postgresql://nodecules:nodecules@localhost:5432/nodecules",
    )


# Base class for ORM models is fine to define at import — it's pure
# Python, no connection required.
Base = declarative_base()


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _get_engine() -> Engine:
    """Return the process-wide engine, creating it on first call."""
    global _engine
    if _engine is None:
        _engine = create_engine(_database_url(), echo=True)
    return _engine


def _get_session_factory() -> sessionmaker[Session]:
    """Return the process-wide session factory, creating it on first call."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=_get_engine()
        )
    return _SessionLocal


def get_database() -> Iterator[Session]:
    """FastAPI dependency: yield a DB session, close on teardown."""
    session = _get_session_factory()()
    try:
        yield session
    finally:
        session.close()


# Backwards-compatible module-level names. These are properties via
# `__getattr__` so attribute access from external code still works but
# nothing binds a connection until someone actually touches the value.
def __getattr__(name: str):
    if name == "engine":
        return _get_engine()
    if name == "SessionLocal":
        return _get_session_factory()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["Base", "get_database"]
