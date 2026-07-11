from sqlalchemy import select

from app.db.models import OutboxEvent
from app.schemas.approval import ApprovalRequestCreate
from app.services.approval_service import ApprovalService
from app.services.outbox import OutboxDispatcher


class FakePublisher:
    def __init__(self):
        self.published = []

    async def publish(self, *, event_type, workspace_id, payload):
        self.published.append((event_type, workspace_id, payload))


async def test_state_change_stages_an_unpublished_outbox_event(sessionmaker):
    async with sessionmaker() as session:
        service = ApprovalService(session)
        payload = ApprovalRequestCreate(
            sourceType="publication", sourceId="pub_1", title="Draft", reviewerUserIds=["usr_1"]
        )
        await service.create(workspace_id="ws_1", actor_id="usr_1", payload=payload, idempotency_key=None)

    async with sessionmaker() as session:
        result = await session.execute(select(OutboxEvent))
        events = result.scalars().all()
        assert len(events) == 1
        assert events[0].event_type == "approval_request.created"
        assert events[0].published_at is None


async def test_dispatcher_publishes_and_marks_events_published(sessionmaker):
    async with sessionmaker() as session:
        service = ApprovalService(session)
        payload = ApprovalRequestCreate(
            sourceType="publication", sourceId="pub_1", title="Draft", reviewerUserIds=["usr_1"]
        )
        await service.create(workspace_id="ws_1", actor_id="usr_1", payload=payload, idempotency_key=None)

    publisher = FakePublisher()
    dispatcher = OutboxDispatcher(sessionmaker, publisher, poll_interval_seconds=999)

    dispatched_count = await dispatcher.dispatch_once()
    assert dispatched_count == 1
    assert len(publisher.published) == 1
    event_type, workspace_id, event_payload = publisher.published[0]
    assert event_type == "approval_request.created"
    assert workspace_id == "ws_1"
    assert event_payload["sourceId"] == "pub_1"

    async with sessionmaker() as session:
        result = await session.execute(select(OutboxEvent))
        events = result.scalars().all()
        assert events[0].published_at is not None

    # A second pass finds nothing left to publish.
    assert await dispatcher.dispatch_once() == 0
    assert len(publisher.published) == 1
