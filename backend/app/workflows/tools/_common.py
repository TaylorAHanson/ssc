"""
Provider getters for workflow tools (monkeypatch points for testing).
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _get_databricks_provider():
    from app.core.config import settings
    from app.core.exceptions import PermanentError
    from app.providers.databricks.client import DatabricksProvider

    host = settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL
    if not host:
        raise PermanentError("DATABRICKS_HOST is required")
    return DatabricksProvider(
        host=host,
        token=settings.DATABRICKS_TOKEN,
        client_id=settings.DATABRICKS_CLIENT_ID,
        client_secret=settings.DATABRICKS_CLIENT_SECRET,
        config={"warehouse_id": settings.DATABRICKS_WAREHOUSE_ID},
    )


def _get_github_provider():
    from app.core.config import settings
    from app.providers.github.client import GitHubProvider

    return GitHubProvider(
        token=settings.GITHUB_TOKEN or settings.get_git_token(),
        org=settings.GITHUB_ORG,
    )


def _get_gitops_provider():
    """Terraform / GitOps-volume provider used for infra plan+apply."""
    from app.providers.gitops.volume import VolumeGitOpsProvider
    return VolumeGitOpsProvider()


def _get_terramate_provider():
    """Terramate API provider used for infrastructure provisioning."""
    from app.providers.terramate.client import TerramateProvider
    return TerramateProvider()


def _get_notification_provider():
    from app.providers.notifications.client import NotificationProvider
    return NotificationProvider()


def _get_identity_provider():
    """Vendor-neutral group-membership provider (noop/rest/lmws via config)."""
    from app.providers.identity import get_identity_provider
    return get_identity_provider()


def _load_request(request_id: Optional[str]):
    """Load the originating request row for a workflow step (or ``(None, None)``).

    Returns an open ``(db, request)`` pair; callers are responsible for closing
    ``db``. Used by tools that must persist back to / read from the request that
    spawned the workflow step (sentinel scan results, allowlist exceptions,
    report bodies). The request id is injected by the ToolExecutor as
    ``_request_id`` (the step's executor scope).
    """
    if not request_id:
        return None, None
    from app.db import RequestModel
    from app.db.session import get_db

    db = next(get_db())
    db.expire_on_commit = False
    request = db.query(RequestModel).filter(RequestModel.id == request_id).first()
    if request is None:
        db.close()
        return None, None
    db.commit()
    return db, request
