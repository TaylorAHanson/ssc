"""
API v1 routes.
"""
from fastapi import APIRouter
from app.api.v1 import requests, agent, approvals, admin, content

router = APIRouter()

# Include sub-routers
router.include_router(requests.router, prefix="/requests", tags=["requests"])
router.include_router(agent.router, prefix="/agent", tags=["agent"])
router.include_router(approvals.router, prefix="/approvals", tags=["approvals"])
router.include_router(admin.router, prefix="/admin", tags=["admin"])
router.include_router(content.router, prefix="/content", tags=["content"])

