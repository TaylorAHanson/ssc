"""
API v1 routes.
"""
from fastapi import APIRouter
from app.api.v1 import requests, agent, approvals, admin, content, delegations, branding, callbacks, users

router = APIRouter()

# Include sub-routers
router.include_router(requests.router, prefix="/requests", tags=["requests"])
router.include_router(agent.router, prefix="/agent", tags=["agent"])
router.include_router(approvals.router, prefix="/approvals", tags=["approvals"])
router.include_router(delegations.router, prefix="/delegations", tags=["delegations"])
router.include_router(users.router, prefix="/users", tags=["users"])
router.include_router(admin.router, prefix="/admin", tags=["admin"])
router.include_router(content.router, prefix="/content", tags=["content"])
router.include_router(branding.router, prefix="/branding", tags=["branding"])
router.include_router(callbacks.router, prefix="/callbacks/terraform", tags=["callbacks"])
# Dev/Test routes
from app.api.v1 import dev
router.include_router(dev.router, prefix="/dev", tags=["dev"])

