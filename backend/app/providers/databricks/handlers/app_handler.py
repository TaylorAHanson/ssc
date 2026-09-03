import asyncio
import fnmatch
import logging
import os
from typing import List, Dict, Any, Optional

from databricks.sdk.service.apps import (
    AppAccessControlRequest,
    AppAccessControlResponse,
    AppPermissionLevel,
)
from app.providers.databricks.handlers.base import BaseResourceHandler

logger = logging.getLogger(__name__)


def is_protected_app(app_name: str, additional_patterns: Optional[List[str]] = None) -> bool:
    """Check whether a Databricks app is protected from automated enforcement.

    Protected apps (including the self-service platform itself and core infrastructure)
    must NEVER be automatically stopped, killed, or have their access revoked.
    """
    if not app_name:
        return True  # Safe default: do not touch empty/unnamed resource

    app_lower = app_name.strip().lower()

    # Core immutable protection patterns for platform apps and companions
    protected_patterns = {
        "edh-ssc*",
        "*edh-ssc*",
        "mcp-server*",
        "*mcp-server*",
    }

    # Environment variables for current deployment
    env_app_name = os.environ.get("DATABRICKS_APP_NAME") or os.environ.get("APP_NAME")
    if env_app_name:
        protected_patterns.add(env_app_name.strip().lower())

    try:
        from app.core.config import settings

        if getattr(settings, "APP_NAME", None):
            protected_patterns.add(settings.APP_NAME.strip().lower())

        configured = getattr(settings, "SENTINEL_PROTECTED_APP_NAMES", "")
        if isinstance(configured, str):
            for pat in configured.split(","):
                if pat.strip():
                    protected_patterns.add(pat.strip().lower())
        elif isinstance(configured, (list, set, tuple)):
            for pat in configured:
                if str(pat).strip():
                    protected_patterns.add(str(pat).strip().lower())
    except Exception:  # noqa: BLE001
        pass

    if additional_patterns:
        for pat in additional_patterns:
            if str(pat).strip():
                protected_patterns.add(str(pat).strip().lower())

    for pattern in protected_patterns:
        if fnmatch.fnmatch(app_lower, pattern):
            return True

    return False


def _acl_dict_to_request(item: Dict[str, Any]) -> Optional[AppAccessControlRequest]:
    """Convert an ACL response dict or snapshot dict into an AppAccessControlRequest."""
    perm_str = item.get("permission_level")
    if not perm_str and "all_permissions" in item:
        for p in item["all_permissions"]:
            if not p.get("inherited", False):
                perm_str = p.get("permission_level")
                break
        if not perm_str and item["all_permissions"]:
            perm_str = item["all_permissions"][0].get("permission_level")

    if not perm_str:
        perm_str = "CAN_USE"

    perm_str = str(perm_str).upper()
    level = (
        AppPermissionLevel[perm_str]
        if perm_str in AppPermissionLevel.__members__
        else AppPermissionLevel.CAN_USE
    )

    user_name = item.get("user_name")
    group_name = item.get("group_name")
    sp_name = item.get("service_principal_name")

    if not user_name and not group_name and not sp_name:
        return None

    return AppAccessControlRequest(
        user_name=user_name,
        group_name=group_name,
        service_principal_name=sp_name,
        permission_level=level,
    )


class AppResourceHandler(BaseResourceHandler):
    """Resource handler for Databricks Apps."""

    @staticmethod
    def is_protected(app_name: str) -> bool:
        """Convenience wrapper for is_protected_app."""
        return is_protected_app(app_name)

    async def discover(self) -> List[Dict[str, Any]]:
        resources = []
        try:
            # Note: Requires databricks-sdk >= 0.20.0 for apps
            apps = await asyncio.to_thread(self.workspace_client.apps.list)
            for app in apps:
                resources.append({
                    "id": app.name,
                    # `app.name` is the human-readable slug and also the id the
                    # delete/kill/stop API takes, so both fields intentionally use it.
                    "name": getattr(app, "display_name", None) or app.name,
                    "type": "app",
                    "owner": getattr(app, "creator", "unknown"),
                    "state": getattr(app.active_deployment, "state", "UNKNOWN")
                    if getattr(app, "active_deployment", None)
                    else "UNKNOWN",
                    "tags": {},
                })
        except Exception as e:
            # Re-raise so the Sentinel attributes this to the workspace + classifies
            # it (auth / permission / network) instead of reporting a silent 0.
            logger.error("Failed to discover apps: %s", e)
            raise
        return resources

    async def stop_and_revoke(
        self, resource_id: str, admin_sp_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """Stop an app and revoke access to all users/owners, granting manage to admins only.

        Returns a dictionary with execution status, snapshot of the previous ACL,
        and the app creator's identity.
        """
        if is_protected_app(resource_id):
            logger.warning("Refusing to stop protected app: %s", resource_id)
            return {
                "status": "skipped_protected",
                "stopped": False,
                "permissions_revoked": False,
                "previous_acl": None,
                "creator": None,
                "message": f"App '{resource_id}' is protected from automated enforcement.",
            }

        # 1. Fetch live app details
        try:
            app = await asyncio.to_thread(self.workspace_client.apps.get, name=resource_id)
        except Exception as e:
            logger.error("Failed to get app %s: %s", resource_id, e)
            raise

        creator = getattr(app, "creator", None)

        # 2. Snapshot current ACL before any changes
        previous_acl: List[Dict[str, Any]] = []
        try:
            current_perms = await asyncio.to_thread(
                self.workspace_client.apps.get_permissions, app_name=resource_id
            )
            acl_list = getattr(current_perms, "access_control_list", None) or []
            for item in acl_list:
                if hasattr(item, "as_dict"):
                    previous_acl.append(item.as_dict())
                elif isinstance(item, dict):
                    previous_acl.append(item)
        except Exception as e:
            logger.warning("Could not read current permissions for app %s: %s", resource_id, e)

        # 3. Stop the app if active
        compute_state = None
        if hasattr(app, "compute_status") and app.compute_status:
            compute_state = getattr(app.compute_status, "state", None)
        state_str = str(getattr(compute_state, "value", compute_state) or "").upper()

        stopped = False
        if state_str not in ("STOPPED", "DELETING", "STOPPING"):
            try:
                await asyncio.to_thread(self.workspace_client.apps.stop, name=resource_id)
                stopped = True
                logger.info("Successfully requested stop for app %s", resource_id)
            except Exception as e:
                err_msg = str(e).lower()
                if "already stopped" in err_msg or "not running" in err_msg or "stopped" in err_msg:
                    logger.info("App %s was already stopped: %s", resource_id, e)
                    stopped = True
                else:
                    logger.error("Failed to stop app %s: %s", resource_id, e)
                    raise
        else:
            stopped = True
            logger.info("App %s is already in state '%s'; skipping stop call", resource_id, state_str)

        # 4. Lock down ACL: only admins group + executing admin SP
        new_acl = [
            AppAccessControlRequest(
                group_name="admins",
                permission_level=AppPermissionLevel.CAN_MANAGE,
            )
        ]

        sp_name = admin_sp_name
        if not sp_name:
            cfg = getattr(self.workspace_client, "config", None)
            sp_name = getattr(cfg, "client_id", None)
        if not sp_name:
            try:
                from app.core.config import settings
                sp_name = getattr(settings, "DATABRICKS_CLIENT_ID", None)
            except Exception:  # noqa: BLE001
                pass

        if sp_name:
            new_acl.append(
                AppAccessControlRequest(
                    service_principal_name=sp_name,
                    permission_level=AppPermissionLevel.CAN_MANAGE,
                )
            )

        try:
            await asyncio.to_thread(
                self.workspace_client.apps.set_permissions,
                app_name=resource_id,
                access_control_list=new_acl,
            )
            logger.info("Successfully locked down permissions for app %s to admins only", resource_id)
        except Exception as e:
            logger.error("Failed to set restricted permissions on app %s: %s", resource_id, e)
            raise

        return {
            "status": "success",
            "stopped": stopped,
            "permissions_revoked": True,
            "previous_acl": previous_acl,
            "creator": creator,
            "message": f"App '{resource_id}' stopped and access revoked to admins only.",
        }

    async def reinstate_permissions(
        self,
        resource_id: str,
        original_acl: Optional[List[Dict[str, Any]]] = None,
        owner: Optional[str] = None,
    ) -> bool:
        """Reinstate permissions on an app from a previous ACL snapshot or to its owner."""
        requests: List[AppAccessControlRequest] = []

        if original_acl:
            for item in original_acl:
                req = _acl_dict_to_request(item)
                if req:
                    requests.append(req)

        # Always ensure admins group retains CAN_MANAGE
        has_admins = any(r.group_name == "admins" for r in requests)
        if not has_admins:
            requests.append(
                AppAccessControlRequest(
                    group_name="admins",
                    permission_level=AppPermissionLevel.CAN_MANAGE,
                )
            )

        # Ensure owner has CAN_MANAGE if specified
        if owner:
            has_owner = any(r.user_name == owner or r.service_principal_name == owner for r in requests)
            if not has_owner:
                if "@" in owner:
                    requests.append(
                        AppAccessControlRequest(
                            user_name=owner,
                            permission_level=AppPermissionLevel.CAN_MANAGE,
                        )
                    )
                else:
                    requests.append(
                        AppAccessControlRequest(
                            service_principal_name=owner,
                            permission_level=AppPermissionLevel.CAN_MANAGE,
                        )
                    )

        try:
            await asyncio.to_thread(
                self.workspace_client.apps.set_permissions,
                app_name=resource_id,
                access_control_list=requests,
            )
            logger.info("Reinstated permissions on app %s with %d ACL entries", resource_id, len(requests))
            return True
        except Exception as e:
            logger.error("Failed to reinstate permissions on app %s: %s", resource_id, e)
            return False

    async def kill(self, resource_id: str) -> bool:
        """Stop app and revoke permissions instead of permanently deleting.

        Permanently deleting an app destroys code, configurations, and state.
        Stopping and revoking permissions fulfills the security policy while
        preserving recoverability.
        """
        try:
            res = await self.stop_and_revoke(resource_id)
            return res.get("status") in ("success", "skipped_protected")
        except Exception as e:
            logger.error("Failed to kill (stop_and_revoke) app %s: %s", resource_id, e)
            return False

    async def warn(self, resource_id: str, message: str) -> bool:
        logger.info("Warning owner of app %s: %s", resource_id, message)
        return True
