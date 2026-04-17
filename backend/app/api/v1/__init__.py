"""
API v1 routes.
"""
from fastapi import APIRouter
from app.api.v1 import requests, agent, approvals, content, delegations, branding, callbacks, users, reports, github, training, allowlist, data_assets

router = APIRouter()

# Include sub-routers
router.include_router(requests.router, prefix="/requests", tags=["requests"])
router.include_router(agent.router, prefix="/agent", tags=["agent"])
router.include_router(approvals.router, prefix="/approvals", tags=["approvals"])
router.include_router(delegations.router, prefix="/delegations", tags=["delegations"])
router.include_router(users.router, prefix="/users", tags=["users"])
router.include_router(content.router, prefix="/content", tags=["content"])
router.include_router(branding.router, prefix="/branding", tags=["branding"])
router.include_router(callbacks.router, prefix="/callbacks/terraform", tags=["callbacks"])
router.include_router(reports.router, prefix="/reports", tags=["reports"])
router.include_router(github.router, prefix="/github", tags=["github"])
router.include_router(training.router, prefix="/training", tags=["training"])
router.include_router(allowlist.router, prefix="/allowlist", tags=["allowlist"])
router.include_router(data_assets.router, prefix="/data-assets", tags=["data-assets"])
# Dev/Test routes
from app.api.v1 import dev
router.include_router(dev.router, prefix="/dev", tags=["dev"])

