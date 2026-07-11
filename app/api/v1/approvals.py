import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, Query, Response, status

from app.api.deps import get_approval_service
from app.config import get_settings
from app.core.auth import AuthContext, enforce_workspace_match, require_permission
from app.core.exceptions import BadRequestError
from app.db.models import ApprovalStatus, SourceType
from app.schemas.approval import (
    ApprovalRequestCreate,
    ApprovalRequestListOut,
    ApprovalRequestOut,
    ApproveDecision,
    CancelDecision,
    RejectDecision,
)
from app.services.approval_service import ApprovalService

_settings = get_settings()

router = APIRouter(prefix="/workspaces/{workspace_id}/approval-requests", tags=["approval-requests"])

IdempotencyKeyHeader = Header(default=None, alias="Idempotency-Key", max_length=255)


@router.post("", response_model=ApprovalRequestOut, status_code=status.HTTP_201_CREATED)
async def create_approval_request(
    workspace_id: str,
    payload: ApprovalRequestCreate,
    response: Response,
    auth: AuthContext = Depends(require_permission("approval:create")),
    service: ApprovalService = Depends(get_approval_service),
    idempotency_key: Optional[str] = IdempotencyKeyHeader,
) -> ApprovalRequestOut:
    enforce_workspace_match(workspace_id, auth)
    result = await service.create(
        workspace_id=workspace_id,
        actor_id=auth.user_id,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    response.status_code = result.status_code
    return result.data


@router.get("", response_model=ApprovalRequestListOut)
async def list_approval_requests(
    workspace_id: str,
    status_filter: Optional[ApprovalStatus] = Query(default=None, alias="status"),
    source_type: Optional[SourceType] = Query(default=None, alias="sourceType"),
    limit: int = Query(default=_settings.default_page_size, ge=1, le=_settings.max_page_size),
    cursor: Optional[str] = Query(default=None),
    auth: AuthContext = Depends(require_permission("approval:read")),
    service: ApprovalService = Depends(get_approval_service),
) -> ApprovalRequestListOut:
    enforce_workspace_match(workspace_id, auth)
    try:
        return await service.list_requests(
            workspace_id=workspace_id,
            status=status_filter,
            source_type=source_type,
            limit=limit,
            cursor=cursor,
        )
    except ValueError as exc:
        raise BadRequestError("Invalid pagination cursor") from exc


@router.get("/{request_id}", response_model=ApprovalRequestOut)
async def get_approval_request(
    workspace_id: str,
    request_id: uuid.UUID,
    auth: AuthContext = Depends(require_permission("approval:read")),
    service: ApprovalService = Depends(get_approval_service),
) -> ApprovalRequestOut:
    enforce_workspace_match(workspace_id, auth)
    return await service.get_request(workspace_id=workspace_id, request_id=request_id)


@router.post("/{request_id}/approve", response_model=ApprovalRequestOut)
async def approve_approval_request(
    workspace_id: str,
    request_id: uuid.UUID,
    response: Response,
    payload: ApproveDecision = ApproveDecision(),
    auth: AuthContext = Depends(require_permission("approval:decide")),
    service: ApprovalService = Depends(get_approval_service),
    idempotency_key: Optional[str] = IdempotencyKeyHeader,
) -> ApprovalRequestOut:
    enforce_workspace_match(workspace_id, auth)
    result = await service.approve(
        workspace_id=workspace_id,
        request_id=request_id,
        actor_id=auth.user_id,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    response.status_code = result.status_code
    return result.data


@router.post("/{request_id}/reject", response_model=ApprovalRequestOut)
async def reject_approval_request(
    workspace_id: str,
    request_id: uuid.UUID,
    payload: RejectDecision,
    response: Response,
    auth: AuthContext = Depends(require_permission("approval:decide")),
    service: ApprovalService = Depends(get_approval_service),
    idempotency_key: Optional[str] = IdempotencyKeyHeader,
) -> ApprovalRequestOut:
    enforce_workspace_match(workspace_id, auth)
    result = await service.reject(
        workspace_id=workspace_id,
        request_id=request_id,
        actor_id=auth.user_id,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    response.status_code = result.status_code
    return result.data


@router.post("/{request_id}/cancel", response_model=ApprovalRequestOut)
async def cancel_approval_request(
    workspace_id: str,
    request_id: uuid.UUID,
    payload: CancelDecision,
    response: Response,
    auth: AuthContext = Depends(require_permission("approval:cancel")),
    service: ApprovalService = Depends(get_approval_service),
    idempotency_key: Optional[str] = IdempotencyKeyHeader,
) -> ApprovalRequestOut:
    enforce_workspace_match(workspace_id, auth)
    result = await service.cancel(
        workspace_id=workspace_id,
        request_id=request_id,
        actor_id=auth.user_id,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    response.status_code = result.status_code
    return result.data
