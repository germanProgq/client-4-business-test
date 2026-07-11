"""Transactional outbox: staging events in the same DB transaction as the
state change they describe, and a background dispatcher that relays
unpublished rows to an `EventPublisher`.

This avoids the classic dual-write problem -- committing the DB change and
publishing to a broker as two separate operations, either of which can fail
independently and leave the two systems inconsistent. Here the write is
atomic; only the relay step can fail, and it is safely retryable because it
just re-reads `published_at IS NULL` rows.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import OutboxEvent
from app.logging_config import log_event
from app.services.events import EventPublisher

logger = logging.getLogger("approval_service.outbox")


def stage(
    session: AsyncSession,
    *,
    workspace_id: str,
    request_id: Optional[UUID],
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """Add an outbox row to the current transaction. Does not commit."""
    session.add(
        OutboxEvent(
            workspace_id=workspace_id,
            request_id=request_id,
            event_type=event_type,
            payload=payload,
        )
    )


class OutboxDispatcher:
    """Polls for unpublished outbox rows and relays them to an
    `EventPublisher`. Runs as a background asyncio task inside the API
    process for this assignment; at real scale this loop would move into
    its own worker deployment so dispatch load never competes with the API
    event loop for request-handling capacity.

    Safe to run from multiple replicas concurrently: `FOR UPDATE SKIP
    LOCKED` (Postgres only -- SQLite has no row-level locking and is only
    ever run single-instance in tests) ensures two dispatchers never pick
    up the same row.
    """

    def __init__(
        self,
        sessionmaker: async_sessionmaker,
        publisher: EventPublisher,
        poll_interval_seconds: float = 2.0,
        batch_size: int = 50,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._publisher = publisher
        self._poll_interval_seconds = poll_interval_seconds
        self._batch_size = batch_size
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        if self._task is None:
            self._stop_event.clear()
            self._task = asyncio.create_task(self._run_forever(), name="outbox-dispatcher")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            await self._task
            self._task = None

    async def _run_forever(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.dispatch_once()
            except Exception:
                logger.exception("outbox dispatch iteration failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def dispatch_once(self) -> int:
        async with self._sessionmaker() as session:
            stmt = (
                select(OutboxEvent)
                .where(OutboxEvent.published_at.is_(None))
                .order_by(OutboxEvent.created_at)
                .limit(self._batch_size)
            )
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                stmt = stmt.with_for_update(skip_locked=True)

            result = await session.execute(stmt)
            events = list(result.scalars().all())

            for event in events:
                await self._publisher.publish(
                    event_type=event.event_type, workspace_id=event.workspace_id, payload=event.payload
                )
                event.published_at = datetime.now(timezone.utc)

            if events:
                await session.commit()
                log_event(logger, logging.INFO, "outbox.dispatched", count=len(events))

            return len(events)
