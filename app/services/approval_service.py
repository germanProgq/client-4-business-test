"""Core business logic for approval requests: creation, listing, and the
approve/reject/cancel state machine.

Every method that mutates data owns a single database transaction that
covers the state change, the audit-log row, the outbox event, and (when a
client sent one) the idempotency-key record. They all commit together or
none of them do -- there is no window where, say, a decision is recorded
but its audit trail is not.

Tenant isolation is enforced here, not just at the HTTP boundary: every
query touching `approval_requests` filters on `workspace_id` pulled from
the authenticated context, never from a client-controlled value alone.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    DuplicateActiveRequestError,
    IdempotencyKeyReuseError,
    InvalidStateTransitionError,
    NotFoundError,
)
from app.core.pagination import encode_cursor, maybe_decode_cursor
from app.db.models import ApprovalRequest, ApprovalReviewer, ApprovalStatus, SourceType
from app.schemas.approval import (
    ApprovalRequestCreate,
    ApprovalRequestListOut,
    ApprovalRequestOut,
    ApproveDecision,
    CancelDecision,
    RejectDecision,
)
from app.services import audit, idempotency, outbox
from app.services.events import EventType

_EVENT_TYPE_BY_ACTION = {
    "approve": EventType.REQUEST_APPROVED,
    "reject": EventType.REQUEST_REJECTED,
    "cancel": EventType.REQUEST_CANCELLED,
}


@dataclass
class ServiceResult:
    status_code: int
    data: ApprovalRequestOut


class ApprovalService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- reads -----------------------------------------------------------

    async def get_request(self, *, workspace_id: str, request_id: uuid.UUID) -> ApprovalRequestOut:
        request = await self._load(workspace_id=workspace_id, request_id=request_id)
        if request is None:
            raise NotFoundError("Approval request not found")
        return ApprovalRequestOut.from_model(request)

    async def list_requests(
        self,
        *,
        workspace_id: str,
        status: Optional[ApprovalStatus],
        source_type: Optional[SourceType],
        limit: int,
        cursor: Optional[str],
    ) -> ApprovalRequestListOut:
        after = maybe_decode_cursor(cursor)

        stmt = select(ApprovalRequest).where(ApprovalRequest.workspace_id == workspace_id)
        if status is not None:
            stmt = stmt.where(ApprovalRequest.status == status)
        if source_type is not None:
            stmt = stmt.where(ApprovalRequest.source_type == source_type)
        if after is not None:
            after_created_at, after_id = after
            stmt = stmt.where(
                (ApprovalRequest.created_at < after_created_at)
                | (
                    (ApprovalRequest.created_at == after_created_at)
                    & (ApprovalRequest.id < after_id)
                )
            )
        stmt = stmt.order_by(ApprovalRequest.created_at.desc(), ApprovalRequest.id.desc()).limit(limit + 1)

        result = await self.session.execute(stmt)
        rows = list(result.scalars().all())

        next_cursor = None
        if len(rows) > limit:
            rows = rows[:limit]
            last = rows[-1]
            next_cursor = encode_cursor(last.created_at, last.id)

        return ApprovalRequestListOut(items=[ApprovalRequestOut.from_model(r) for r in rows], next_cursor=next_cursor)

    # -- writes ------------------------------------------------------------

    async def create(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        payload: ApprovalRequestCreate,
        idempotency_key: Optional[str],
    ) -> ServiceResult:
        scope = "approval_request.create"
        fp = idempotency.fingerprint(payload.model_dump(mode="json", by_alias=True))

        if idempotency_key:
            replay = await self._replay_if_present(workspace_id, idempotency_key, scope, fp)
            if replay is not None:
                return replay

        request = ApprovalRequest(
            workspace_id=workspace_id,
            source_type=payload.source_type,
            source_id=payload.source_id,
            title=payload.title,
            description=payload.description,
            created_by=actor_id,
            status=ApprovalStatus.PENDING,
        )
        request.reviewers = [ApprovalReviewer(user_id=uid) for uid in payload.reviewer_user_ids]
        self.session.add(request)

        try:
            # id/status/version/created_at/updated_at are all Python-side
            # defaults (see app.db.models), so they're already populated on
            # `request` right after flush -- no round-trip refresh needed.
            await self.session.flush()
            out = ApprovalRequestOut.from_model(request)
            response_body = out.model_dump(mode="json", by_alias=True)

            audit.record(
                self.session,
                request_id=request.id,
                workspace_id=workspace_id,
                actor_id=actor_id,
                action="create",
                previous_state=None,
                new_state=response_body,
            )
            outbox.stage(
                self.session,
                workspace_id=workspace_id,
                request_id=request.id,
                event_type=EventType.REQUEST_CREATED,
                payload=response_body,
            )
            if idempotency_key:
                idempotency.stage(
                    self.session,
                    workspace_id=workspace_id,
                    key=idempotency_key,
                    scope=scope,
                    request_fingerprint=fp,
                    response_status_code=201,
                    response_body=response_body,
                )
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            if idempotency_key:
                replay = await self._replay_if_present(workspace_id, idempotency_key, scope, fp)
                if replay is not None:
                    return replay
            existing_active = await self._find_active_request(
                workspace_id=workspace_id, source_type=payload.source_type, source_id=payload.source_id
            )
            raise DuplicateActiveRequestError(
                "An active approval request already exists for this source",
                details={"existingRequestId": str(existing_active.id) if existing_active else None},
            ) from exc

        return ServiceResult(201, out)

    async def approve(
        self, *, workspace_id: str, request_id: uuid.UUID, actor_id: str, payload: ApproveDecision, idempotency_key: Optional[str]
    ) -> ServiceResult:
        return await self._decide(
            workspace_id=workspace_id,
            request_id=request_id,
            actor_id=actor_id,
            action="approve",
            new_status=ApprovalStatus.APPROVED,
            resolution_note=payload.comment,
            idempotency_payload=payload.model_dump(mode="json", by_alias=True),
            idempotency_key=idempotency_key,
        )

    async def reject(
        self, *, workspace_id: str, request_id: uuid.UUID, actor_id: str, payload: RejectDecision, idempotency_key: Optional[str]
    ) -> ServiceResult:
        return await self._decide(
            workspace_id=workspace_id,
            request_id=request_id,
            actor_id=actor_id,
            action="reject",
            new_status=ApprovalStatus.REJECTED,
            resolution_note=payload.reason,
            idempotency_payload=payload.model_dump(mode="json", by_alias=True),
            idempotency_key=idempotency_key,
        )

    async def cancel(
        self, *, workspace_id: str, request_id: uuid.UUID, actor_id: str, payload: CancelDecision, idempotency_key: Optional[str]
    ) -> ServiceResult:
        return await self._decide(
            workspace_id=workspace_id,
            request_id=request_id,
            actor_id=actor_id,
            action="cancel",
            new_status=ApprovalStatus.CANCELLED,
            resolution_note=payload.reason,
            idempotency_payload=payload.model_dump(mode="json", by_alias=True),
            idempotency_key=idempotency_key,
        )

    # -- internals -----------------------------------------------------------

    async def _decide(
        self,
        *,
        workspace_id: str,
        request_id: uuid.UUID,
        actor_id: str,
        action: str,
        new_status: ApprovalStatus,
        resolution_note: Optional[str],
        idempotency_payload: dict[str, Any],
        idempotency_key: Optional[str],
    ) -> ServiceResult:
        scope = f"approval_request.{action}"
        fp = idempotency.fingerprint({"request_id": str(request_id), **idempotency_payload})

        if idempotency_key:
            replay = await self._replay_if_present(workspace_id, idempotency_key, scope, fp)
            if replay is not None:
                return replay

        request = await self._load(workspace_id=workspace_id, request_id=request_id)
        if request is None:
            raise NotFoundError("Approval request not found")

        if request.status != ApprovalStatus.PENDING:
            raise InvalidStateTransitionError(
                f"Request is already '{request.status.value}' and cannot be changed",
                details={"currentStatus": request.status.value},
            )

        previous_state = ApprovalRequestOut.from_model(request).model_dump(mode="json", by_alias=True)
        expected_version = request.version
        now = datetime.now(timezone.utc)

        # Atomic transition guard + optimistic lock in one statement: if
        # another request already flipped this row's status (or bumped its
        # version) between our SELECT above and this UPDATE, rowcount is 0
        # and we treat it as a lost race rather than corrupting state.
        update_stmt = (
            update(ApprovalRequest)
            .where(
                ApprovalRequest.id == request_id,
                ApprovalRequest.workspace_id == workspace_id,
                ApprovalRequest.status == ApprovalStatus.PENDING,
                ApprovalRequest.version == expected_version,
            )
            .values(
                status=new_status,
                version=ApprovalRequest.version + 1,
                decided_by=actor_id,
                decided_at=now,
                resolution_note=resolution_note,
                updated_at=now,
            )
        )

        try:
            exec_result = await self.session.execute(update_stmt)
            if exec_result.rowcount == 0:
                await self.session.rollback()
                raise InvalidStateTransitionError(
                    "Request was concurrently modified by another decision; please retry",
                    details={"requestId": str(request_id)},
                )

            await self.session.refresh(request)
            out = ApprovalRequestOut.from_model(request)
            response_body = out.model_dump(mode="json", by_alias=True)

            audit.record(
                self.session,
                request_id=request.id,
                workspace_id=workspace_id,
                actor_id=actor_id,
                action=action,
                previous_state=previous_state,
                new_state=response_body,
            )
            outbox.stage(
                self.session,
                workspace_id=workspace_id,
                request_id=request.id,
                event_type=_EVENT_TYPE_BY_ACTION[action],
                payload=response_body,
            )
            if idempotency_key:
                idempotency.stage(
                    self.session,
                    workspace_id=workspace_id,
                    key=idempotency_key,
                    scope=scope,
                    request_fingerprint=fp,
                    response_status_code=200,
                    response_body=response_body,
                )
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            if idempotency_key:
                replay = await self._replay_if_present(workspace_id, idempotency_key, scope, fp)
                if replay is not None:
                    return replay
            raise

        return ServiceResult(200, out)

    async def _load(self, *, workspace_id: str, request_id: uuid.UUID) -> Optional[ApprovalRequest]:
        stmt = select(ApprovalRequest).where(
            ApprovalRequest.id == request_id, ApprovalRequest.workspace_id == workspace_id
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _find_active_request(
        self, *, workspace_id: str, source_type: SourceType, source_id: str
    ) -> Optional[ApprovalRequest]:
        stmt = select(ApprovalRequest).where(
            ApprovalRequest.workspace_id == workspace_id,
            ApprovalRequest.source_type == source_type,
            ApprovalRequest.source_id == source_id,
            ApprovalRequest.status == ApprovalStatus.PENDING,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _replay_if_present(
        self, workspace_id: str, key: str, scope: str, fp: str
    ) -> Optional[ServiceResult]:
        existing = await idempotency.find_existing(self.session, workspace_id=workspace_id, key=key, scope=scope)
        if existing is None:
            return None
        if existing.request_fingerprint != fp:
            raise IdempotencyKeyReuseError(
                "Idempotency-Key was already used with a different request body",
                details={"idempotencyKey": key},
            )
        return ServiceResult(existing.response_status_code, ApprovalRequestOut.model_validate(existing.response_body))
