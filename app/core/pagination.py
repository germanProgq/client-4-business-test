"""Opaque keyset-pagination cursors for the approval-requests list endpoint.

Keyset pagination (as opposed to OFFSET/LIMIT) keeps list queries on the
``idx_requests_workspace_status`` index at any page depth, which matters
once a workspace has accumulated a large history of requests.
"""

import base64
import binascii
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

_SEPARATOR = "|"


def encode_cursor(created_at: datetime, request_id: UUID) -> str:
    # SQLite's DateTime type round-trips values as naive (it has no native
    # tz-aware storage), even though the application only ever writes UTC
    # instants (see app.db.models._utcnow). Postgres returns proper
    # tz-aware UTC datetimes. `.astimezone()` on a naive datetime assumes
    # it is in the *local* timezone, which would silently shift a
    # naive-but-actually-UTC value from SQLite -- so naive values are
    # tagged as UTC directly instead of converted.
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    else:
        created_at = created_at.astimezone(timezone.utc)
    raw = f"{created_at.isoformat()}{_SEPARATOR}{request_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        created_at_raw, request_id_raw = raw.split(_SEPARATOR)
        return datetime.fromisoformat(created_at_raw), UUID(request_id_raw)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise ValueError("Invalid pagination cursor") from exc


def maybe_decode_cursor(cursor: Optional[str]) -> Optional[tuple[datetime, UUID]]:
    if not cursor:
        return None
    return decode_cursor(cursor)
