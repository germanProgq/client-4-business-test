from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.services.approval_service import ApprovalService


async def get_approval_service(session: AsyncSession = Depends(get_db_session)) -> ApprovalService:
    return ApprovalService(session)
