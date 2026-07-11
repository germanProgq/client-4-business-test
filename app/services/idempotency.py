"""Idempotency-key handling shared by every mutating service method.

The unique constraint on (workspace_id, idempotency_key, scope) is the
actual source of truth -- concurrent duplicate requests both racing past
the `find_existing` check will have one of them fail on flush/commit with
an IntegrityError. Callers should treat that as "someone else just won the
race" and retry the lookup (see ApprovalService).
"""

import hashlib
import json
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IdempotencyKey


def fingerprint(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def find_existing(
    session: AsyncSession, *, workspace_id: str, key: str, scope: str
) -> Optional[IdempotencyKey]:
    stmt = select(IdempotencyKey).where(
        IdempotencyKey.workspace_id == workspace_id,
        IdempotencyKey.idempotency_key == key,
        IdempotencyKey.scope == scope,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


def stage(
    session: AsyncSession,
    *,
    workspace_id: str,
    key: str,
    scope: str,
    request_fingerprint: str,
    response_status_code: int,
    response_body: Any,
) -> None:
    """Add the idempotency record to the session without committing --
    callers persist it as part of the same transaction as the state
    change it guards."""
    session.add(
        IdempotencyKey(
            workspace_id=workspace_id,
            idempotency_key=key,
            scope=scope,
            request_fingerprint=request_fingerprint,
            response_status_code=response_status_code,
            response_body=response_body,
        )
    )
