"""Initial schema: approval_requests, approval_reviewers,
approval_audit_logs, idempotency_keys, outbox_events.

Revision ID: 0001
Revises:
Create Date: 2026-07-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

source_type_enum = postgresql.ENUM(
    "publication", "scenario", "edit", "external", name="source_type"
)
approval_status_enum = postgresql.ENUM(
    "pending", "approved", "rejected", "cancelled", name="approval_status"
)


def upgrade() -> None:
    bind = op.get_bind()
    source_type_enum.create(bind, checkfirst=True)
    approval_status_enum.create(bind, checkfirst=True)

    op.create_table(
        "approval_requests",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", sa.String(255), nullable=False),
        sa.Column("source_type", source_type_enum, nullable=False),
        sa.Column("source_id", sa.String(255), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", approval_status_enum, nullable=False, server_default="pending"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("decided_by", sa.String(255), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "(status = 'pending' AND decided_by IS NULL AND decided_at IS NULL) OR "
            "(status != 'pending' AND decided_by IS NOT NULL AND decided_at IS NOT NULL)",
            name="ck_requests_decision_consistency",
        ),
    )
    op.create_index(
        "idx_requests_workspace_status", "approval_requests", ["workspace_id", "status", "created_at"]
    )
    op.create_index(
        "idx_requests_workspace_created", "approval_requests", ["workspace_id", "created_at"]
    )
    op.create_index("idx_requests_workspace_id_pk", "approval_requests", ["workspace_id", "id"])
    op.create_index(
        "idx_unique_active_request",
        "approval_requests",
        ["workspace_id", "source_type", "source_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.create_table(
        "approval_reviewers",
        sa.Column(
            "request_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("approval_requests.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("user_id", sa.String(255), primary_key=True),
    )

    op.create_table(
        "approval_audit_logs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "request_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("approval_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.String(255), nullable=False),
        sa.Column("actor_id", sa.String(255), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("previous_state", postgresql.JSONB(), nullable=True),
        sa.Column("new_state", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index(
        "idx_audit_workspace_request", "approval_audit_logs", ["workspace_id", "request_id", "created_at"]
    )

    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", sa.String(255), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("scope", sa.String(100), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("response_status_code", sa.Integer(), nullable=False),
        sa.Column("response_body", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("workspace_id", "idempotency_key", "scope", name="uq_idempotency_key_scope"),
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", sa.String(255), nullable=False),
        sa.Column(
            "request_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("approval_requests.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_outbox_unpublished",
        "outbox_events",
        ["created_at"],
        postgresql_where=sa.text("published_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("outbox_events")
    op.drop_table("idempotency_keys")
    op.drop_table("approval_audit_logs")
    op.drop_table("approval_reviewers")
    op.drop_table("approval_requests")

    bind = op.get_bind()
    approval_status_enum.drop(bind, checkfirst=True)
    source_type_enum.drop(bind, checkfirst=True)
