"""Event-bus integration point.

No real broker is wired up for this assignment (the task explicitly says
not to add real external services), but the service is structured so a
Kafka/RabbitMQ producer is a drop-in: implement `EventPublisher` and pass
it to `OutboxDispatcher` instead of `LoggingEventPublisher`. See
DESIGN.md for the outbox pattern this sits behind and the proposed topic
layout.
"""

import logging
from typing import Any, Protocol

from app.core.sanitize import sanitize_value
from app.logging_config import log_event

logger = logging.getLogger("approval_service.events")


class EventType:
    REQUEST_CREATED = "approval_request.created"
    REQUEST_APPROVED = "approval_request.approved"
    REQUEST_REJECTED = "approval_request.rejected"
    REQUEST_CANCELLED = "approval_request.cancelled"


class EventPublisher(Protocol):
    async def publish(self, *, event_type: str, workspace_id: str, payload: dict[str, Any]) -> None: ...


class LoggingEventPublisher:
    """Default local/dev publisher. Emits a sanitized structured log line
    for every event instead of pushing to a broker."""

    async def publish(self, *, event_type: str, workspace_id: str, payload: dict[str, Any]) -> None:
        log_event(
            logger,
            logging.INFO,
            "event.published",
            event_type=event_type,
            workspace_id=workspace_id,
            payload=sanitize_value(payload),
        )
