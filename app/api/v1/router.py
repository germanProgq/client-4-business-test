from fastapi import APIRouter

from app.api.v1 import approvals

router = APIRouter()
router.include_router(approvals.router)
