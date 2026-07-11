import enum
import uuid
from datetime import datetime, timezone
from typing import Any, List, Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON, Enum as SAEnum, Uuid

from app.db.base import Base

JSONVariant = JSON().with_variant(JSONB, "postgresql")


class SourceType(str, enum.Enum):
    PUBLICATION = "publication"
    SCENARIO = "scenario"
    EDIT = "edit"
    EXTERNAL = "external"


class ApprovalStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


FINAL_STATUSES = frozenset({ApprovalStatus.APPROVED, ApprovalStatus.REJECTED, ApprovalStatus.CANCELLED})


def _source_type_enum() -> SAEnum:
    return SAEnum(SourceType, name="source_type", values_callable=lambda enum_cls: [e.value for e in enum_cls])


def _approval_status_enum() -> SAEnum:
    return SAEnum(
        ApprovalStatus, name="approval_status", values_callable=lambda enum_cls: [e.value for e in enum_cls]
    )


def _utcnow() -> datetime:
    # Computed in Python (microsecond resolution everywhere) rather than
    # via a server_default: SQLite's CURRENT_TIMESTAMP only has 1-second
    # resolution, which made same-second inserts tie and fall back to
    # sorting by UUID -- effectively random order -- in the list endpoint.
    # A DB-side server_default is still kept in the migration as a safety
    # net for any insert issued outside the ORM.
    return datetime.now(timezone.utc)


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Tenant boundary. Every query against this table must filter on this
    # column -- see app.services.approval_service for the single choke
    # point where requests are read/written.
    workspace_id: Mapped[str] = mapped_column(String(255), nullable=False)

    source_type: Mapped[SourceType] = mapped_column(_source_type_enum(), nullable=False)
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)

    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    status: Mapped[ApprovalStatus] = mapped_column(
        _approval_status_enum(), nullable=False, default=ApprovalStatus.PENDING
    )
    # Bumped on every successful transition; combined with the `status`
    # transition guard in the UPDATE statement to detect concurrent
    # decisions on the same request (see approval_service.decide()).
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    decided_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, server_default=func.now()
    )

    reviewers: Mapped[List["ApprovalReviewer"]] = relationship(
        back_populates="request", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        # DB-level invariant, independent of application bugs: a request is
        # either still pending with no decision recorded, or final with a
        # decision fully recorded. Catches a broken transition (e.g. status
        # flipped without setting decided_by/decided_at) even if it slips
        # past the service layer.
        CheckConstraint(
            "(status = 'pending' AND decided_by IS NULL AND decided_at IS NULL) OR "
            "(status != 'pending' AND decided_by IS NOT NULL AND decided_at IS NOT NULL)",
            name="ck_requests_decision_consistency",
        ),
        # Serves GET /workspaces/{id}/approval-requests (filter by tenant +
        # status, ordered by recency) as a single index scan.
        Index("idx_requests_workspace_status", "workspace_id", "status", "created_at"),
        # Same endpoint without a status filter (the common "show me
        # everything" view) -- (workspace_id, status, created_at) alone
        # cannot serve an ORDER BY created_at without a filter on status in
        # between, so list queries with no status filter get their own
        # covering index instead of falling back to a sort.
        Index("idx_requests_workspace_created", "workspace_id", "created_at"),
        # Keeps GET .../approval-requests/{request_id} fully index-covered
        # even though the isolation check (workspace_id match) is
        # re-verified in application code regardless of index shape.
        Index("idx_requests_workspace_id_pk", "workspace_id", "id"),
        # At most one active (pending) request per source entity. Lets a
        # second create-attempt for the same source fail fast with a clear
        # 409 instead of silently piling up parallel approvals.
        Index(
            "idx_unique_active_request",
            "workspace_id",
            "source_type",
            "source_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
            sqlite_where=text("status = 'pending'"),
        ),
    )


class ApprovalReviewer(Base):
    __tablename__ = "approval_reviewers"

    request_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("approval_requests.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(String(255), primary_key=True)

    request: Mapped["ApprovalRequest"] = relationship(back_populates="reviewers")


class ApprovalAuditLog(Base):
    __tablename__ = "approval_audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("approval_requests.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    previous_state: Mapped[Optional[dict]] = mapped_column(JSONVariant, nullable=True)
    new_state: Mapped[Optional[dict]] = mapped_column(JSONVariant, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )

    __table_args__ = (Index("idx_audit_workspace_request", "workspace_id", "request_id", "created_at"),)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    # Identifies which operation the key was scoped to (e.g.
    # "approval_request.create", "approval_request.approve") so the same
    # key value cannot be silently replayed across unrelated endpoints.
    scope: Mapped[str] = mapped_column(String(100), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[Any] = mapped_column(JSONVariant, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", "idempotency_key", "scope", name="uq_idempotency_key_scope"),
    )


class OutboxEvent(Base):
    """Transactional outbox: rows are inserted in the same DB transaction
    as the state change they describe, then relayed to the event bus by a
    separate dispatcher (see app.services.outbox_dispatcher). This avoids
    the dual-write problem of committing a state change and publishing to
    a broker as two independent operations.
    """

    __tablename__ = "outbox_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[str] = mapped_column(String(255), nullable=False)
    request_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("approval_requests.id", ondelete="CASCADE"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[Any] = mapped_column(JSONVariant, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "idx_outbox_unpublished",
            "created_at",
            postgresql_where=text("published_at IS NULL"),
            sqlite_where=text("published_at IS NULL"),
        ),
    )
