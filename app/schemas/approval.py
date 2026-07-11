import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import Field, field_validator

from app.db.models import ApprovalRequest, ApprovalStatus, SourceType
from app.schemas.common import CamelModel

MAX_REVIEWERS = 100


def _clean_id_list(values: List[str]) -> List[str]:
    seen: set[str] = set()
    cleaned: List[str] = []
    for raw in values:
        value = raw.strip()
        if value and value not in seen:
            seen.add(value)
            cleaned.append(value)
    return cleaned


class ApprovalRequestCreate(CamelModel):
    source_type: SourceType
    source_id: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=500)
    description: Optional[str] = Field(default=None, max_length=10000)
    reviewer_user_ids: List[str] = Field(default_factory=list)

    @field_validator("source_id", "title")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("reviewer_user_ids")
    @classmethod
    def _clean_reviewers(cls, value: List[str]) -> List[str]:
        cleaned = _clean_id_list(value)
        if len(cleaned) > MAX_REVIEWERS:
            raise ValueError(f"at most {MAX_REVIEWERS} reviewers are supported")
        return cleaned


class ApproveDecision(CamelModel):
    comment: Optional[str] = Field(default=None, max_length=2000)


class RejectDecision(CamelModel):
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def _strip_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class CancelDecision(CamelModel):
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def _strip_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class ApprovalRequestOut(CamelModel):
    id: uuid.UUID
    workspace_id: str
    source_type: SourceType
    source_id: str
    title: str
    description: Optional[str]
    status: ApprovalStatus
    reviewer_user_ids: List[str]
    created_by: str
    decided_by: Optional[str]
    decided_at: Optional[datetime]
    resolution_note: Optional[str]
    version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, model: ApprovalRequest) -> "ApprovalRequestOut":
        return cls(
            id=model.id,
            workspace_id=model.workspace_id,
            source_type=model.source_type,
            source_id=model.source_id,
            title=model.title,
            description=model.description,
            status=model.status,
            reviewer_user_ids=[reviewer.user_id for reviewer in model.reviewers],
            created_by=model.created_by,
            decided_by=model.decided_by,
            decided_at=model.decided_at,
            resolution_note=model.resolution_note,
            version=model.version,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )


class ApprovalRequestListOut(CamelModel):
    items: List[ApprovalRequestOut]
    next_cursor: Optional[str] = None
