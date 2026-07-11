from typing import AsyncIterator, Optional

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.main import create_app

ALL_PERMISSIONS = ["approval:read", "approval:create", "approval:decide", "approval:cancel"]


@pytest.fixture
async def sessionmaker() -> AsyncIterator[async_sessionmaker]:
    """A fresh in-memory SQLite database per test. StaticPool keeps a
    single connection alive for the engine's lifetime so every session
    sees the same in-memory database instead of each getting its own
    (which is SQLite's default, and would look like an empty DB)."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
    try:
        yield maker
    finally:
        await engine.dispose()


@pytest.fixture
def app(sessionmaker):
    # The outbox dispatcher is exercised directly in test_outbox.py; the
    # HTTP-level tests don't need a live background poller.
    return create_app(sessionmaker=sessionmaker, start_outbox_dispatcher=False)


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def auth_headers(
    workspace_id: str = "ws_1",
    user_id: str = "usr_1",
    permissions: Optional[list[str]] = None,
) -> dict:
    scopes = ALL_PERMISSIONS if permissions is None else permissions
    return {
        "X-Workspace-Id": workspace_id,
        "X-User-Id": user_id,
        "X-User-Permissions": ",".join(scopes),
    }


def create_payload(**overrides) -> dict:
    payload = {
        "sourceType": "publication",
        "sourceId": "pub_123",
        "title": "Instagram reel draft",
        "description": "Needs final approval",
        "reviewerUserIds": ["usr_1", "usr_2"],
    }
    payload.update(overrides)
    return payload
