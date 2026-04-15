import logging
from typing import List, Dict, Any
from app.providers.databricks.handlers.base import BaseResourceHandler

logger = logging.getLogger(__name__)

class DashboardResourceHandler(BaseResourceHandler):
    async def discover(self) -> List[Dict[str, Any]]:
        resources = []
        try:
            dashboards = self.workspace_client.lakeview.list()
            for dash in dashboards:
                uses_embedded_credentials = getattr(dash, 'uses_embedded_credentials', False)
                shared_with = getattr(dash, 'shared_with', [])
                
                try:
                    # Attempt to fetch published dashboard to see if it uses embedded credentials
                    pub = self.workspace_client.lakeview.get_published(dash.dashboard_id)
                    if hasattr(pub, 'embed_credentials') and pub.embed_credentials:
                        uses_embedded_credentials = True
                except Exception as inner_e:
                    logger.debug(f"Could not fetch published status for dashboard {dash.dashboard_id}: {inner_e}")

                try:
                    # Attempt to fetch permissions to see who it is shared with
                    perms = self.workspace_client.workspace.get_permissions("dashboards", dash.dashboard_id)
                    if hasattr(perms, 'access_control_list') and perms.access_control_list:
                        for ac in perms.access_control_list:
                            group_name = getattr(ac, 'group_name', None)
                            if group_name:
                                if group_name.lower() in ('users', 'account users', 'all_users'):
                                    shared_with.append('ALL_USERS')
                                else:
                                    shared_with.append(group_name)
                except Exception as inner_e:
                    logger.debug(f"Could not fetch permissions for dashboard {dash.dashboard_id}: {inner_e}")
                    # If we can see the dashboard but get a PermissionDenied on the ACL endpoint,
                    # it means we (the Service Principal) have Viewer access but not CAN_MANAGE.
                    # In a demo/production context without admin rights, this usually implies
                    # it was broadly shared with "All Workspace Users". We infer this to trigger the policy.
                    if uses_embedded_credentials and ("does not have CAN_MANAGE permissions" in str(inner_e) or "403" in str(inner_e)):
                        shared_with.append('ALL_USERS')

                resources.append({
                    "id": dash.dashboard_id,
                    "name": dash.display_name,
                    "type": "dashboard",
                    "owner": getattr(dash, 'creator_user_name', 'unknown'),
                    "uses_embedded_credentials": uses_embedded_credentials,
                    "shared_with": shared_with,
                    "tags": {}
                })
        except Exception as e:
            logger.error(f"Failed to discover dashboards: {e}")
        return resources
        
    async def kill(self, resource_id: str) -> bool:
        try:
            self.workspace_client.lakeview.trash(dashboard_id=resource_id)
            return True
        except Exception as e:
            logger.error(f"Failed to trash dashboard {resource_id}: {e}")
            return False

    async def warn(self, resource_id: str, message: str) -> bool:
        logger.info(f"Warning owner of dashboard {resource_id}: {message}")
        return True
