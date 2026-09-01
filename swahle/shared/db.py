"""Engine/session factory shared by every module.

For now the ORM models are reused from the legacy ``turtle_db.db`` package so
the new app and the still-running Tornado app can never drift apart on schema
while both point at the same MariaDB. When the legacy app is retired these
models move into ``modules/turtle_db/models.py``.
"""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.settings import settings

engine = create_engine(
    settings.database_uri,
    pool_pre_ping=True,   # survive MariaDB idle-connection drops
    pool_recycle=3600,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
