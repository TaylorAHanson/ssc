"""
API v1 routes.
"""
from fastapi import APIRouter
from app.api.v1 import requests, agent, agent_polls, approvals, content, delegations, branding, callbacks, roles, reports, github, training, allowlist, data_assets, data_contracts, odps, system, tags, context_catalog

router = APIRouter()

# Include sub-routers
router.include_router(system.router, prefix="/system", tags=["system"])
router.include_router(requests.router, prefix="/requests", tags=["requests"])
router.include_router(agent.router, prefix="/agent", tags=["agent"])
# Poll endpoints for asynchronous agent tools (Genie, etc.). Mounted at
# /agent/poll/* so they're discoverable next to the conversation routes.
router.include_router(agent_polls.router, prefix="/agent/poll", tags=["agent"])
router.include_router(approvals.router, prefix="/approvals", tags=["approvals"])
router.include_router(delegations.router, prefix="/delegations", tags=["delegations"])
router.include_router(roles.router, prefix="/roles", tags=["roles"])
router.include_router(content.router, prefix="/content", tags=["content"])
router.include_router(branding.router, prefix="/branding", tags=["branding"])
router.include_router(callbacks.router, prefix="/callbacks/terraform", tags=["callbacks"])
router.include_router(reports.router, prefix="/reports", tags=["reports"])
router.include_router(github.router, prefix="/github", tags=["github"])
router.include_router(training.router, prefix="/training", tags=["training"])
router.include_router(allowlist.router, prefix="/allowlist", tags=["allowlist"])
router.include_router(data_assets.router, prefix="/data-assets", tags=["data-assets"])
router.include_router(data_contracts.router, prefix="/data-contracts", tags=["data-contracts"])
router.include_router(odps.router, prefix="/odps", tags=["odps"])
router.include_router(tags.router, prefix="/tags", tags=["tags"])
router.include_router(context_catalog.router, prefix="/context", tags=["context-catalog"])
# Dev/Test routes
from app.api.v1 import dev
router.include_router(dev.router, prefix="/dev", tags=["dev"])

