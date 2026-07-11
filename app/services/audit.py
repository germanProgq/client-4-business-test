import uuid
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sanitize import sanitize_value
from app.db.models import ApprovalAuditLog


def record(
    session: AsyncSession,
    *,
    request_id: uuid.UUID,
    workspace_id: str,
    actor_id: str,
    action: str,
    previous_state: Optional[dict[str, Any]],
    new_state: Optional[dict[str, Any]],
) -> None:
    """Stage an audit row in the current transaction. Every successful
    mutation calls this before commit, so the audit trail and the change it
    describes are always atomic (either both land or neither does)."""
    session.add(
        ApprovalAuditLog(
            request_id=request_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            action=action,
            previous_state=sanitize_value(previous_state),
            new_state=sanitize_value(new_state),
        )
    )
