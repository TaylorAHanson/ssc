"""
Tool to check overprovisioned users.
"""
from typing import Dict, Any, Literal
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.providers.databricks import DatabricksProvider
from app.core.config import settings
from app.core.exceptions import RetryableError

class CheckOverprovisionedUsersInput(BaseModel):
    check_type: Literal["risk_score", "grants_summary", "workspace_admins"] = Field(
        "risk_score", 
        description="The type of audit to perform. 'risk_score' (recommended) identifies users with access who don't use it. 'grants_summary' shows total privilege counts. 'workspace_admins' lists members of the admins group."
    )

@tool(
    name="check_overprovisioned_users",
    description="""Identifies users with excessive privileges or high-risk profiles using a score-based model.
Capabilities:
1) Risk Score Assessment: Combines privilege counts with activity data (from audit logs) to find users with high access but low usage.
2) Grants Summary: Provides a breakdown of total grants across all Unity Catalog securables (catalogs, schemas, tables, etc.).
3) Admin Audit: Identifies members of the 'admins' group.""",
    required_role="governance_admin",
    args_schema=CheckOverprovisionedUsersInput
)
async def check_overprovisioned_users(check_type: str = "risk_score", **kwargs) -> Dict[str, Any]:
    try:
        # Read-only governance scan runs as the calling user (OBO) when
        # available; falls back to the service principal otherwise.
        obo_token = kwargs.get("_obo_token")
        provider = DatabricksProvider(
            host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL,
            token=settings.DATABRICKS_TOKEN,
            client_id=settings.DATABRICKS_CLIENT_ID,
            client_secret=settings.DATABRICKS_CLIENT_SECRET,
            config={"warehouse_id": settings.DATABRICKS_WAREHOUSE_ID}
        )

        if check_type == "risk_score":
            query = """
            WITH catalog_privileges AS (
              SELECT lower(grantee) AS email, 'catalog' AS securable_type, count(*) AS total
              FROM system.information_schema.catalog_privileges GROUP BY 1, 2
            ),
            external_location_privileges AS (
              SELECT lower(grantee) AS email, 'external_location' AS securable_type, count(*) AS total
              FROM system.information_schema.external_location_privileges GROUP BY 1, 2
            ),
            metastore_privileges AS (
              SELECT lower(grantee) AS email, 'metastore' AS securable_type, count(*) AS total
              FROM system.information_schema.metastore_privileges GROUP BY 1, 2
            ),
            routine_privileges AS (
              SELECT lower(grantee) AS email, 'function' AS securable_type, count(*) AS total
              FROM system.information_schema.routine_privileges GROUP BY 1, 2
            ),
            schema_privileges AS (
              SELECT lower(grantee) AS email, 'schema' AS securable_type, count(*) AS total
              FROM system.information_schema.schema_privileges GROUP BY 1, 2
            ),
            storage_credential_privileges AS (
              SELECT lower(grantee) AS email, 'storage_credential' AS securable_type, count(*) AS total
              FROM system.information_schema.storage_credential_privileges GROUP BY 1, 2
            ),
            table_privileges AS (
              SELECT lower(grantee) AS email, 'table' AS securable_type, count(*) AS total
              FROM system.information_schema.table_privileges GROUP BY 1, 2
            ),
            volume_privileges AS (
              SELECT lower(grantee) AS email, 'volume' AS securable_type, count(*) AS total
              FROM system.information_schema.volume_privileges GROUP BY 1, 2
            ),
            privileged_users AS (
              SELECT email, SUM(total) AS privilege_grants
              FROM (
                SELECT * FROM catalog_privileges
                UNION ALL SELECT * FROM external_location_privileges
                UNION ALL SELECT * FROM metastore_privileges
                UNION ALL SELECT * FROM routine_privileges
                UNION ALL SELECT * FROM schema_privileges
                UNION ALL SELECT * FROM storage_credential_privileges
                UNION ALL SELECT * FROM table_privileges
                UNION ALL SELECT * FROM volume_privileges
              )
              GROUP BY 1
            ),
            admin_like_activity AS (
              SELECT
                lower(coalesce(user_identity.email, request_params.user)) AS email,
                count(*) AS admin_actions_last_90d
              FROM system.access.audit
              WHERE event_date >= current_date() - INTERVAL 90 DAYS
                AND action_name IN (
                  'addPrincipalToGroup','changeDatabricksSqlAcl','changeDatabricksWorkspaceAcl','changeDbTokenAcl',
                  'changePasswordAcl','changeServicePrincipalAcls','generateDbToken','setAdmin','changeClusterAcl',
                  'changeClusterPolicyAcl','changeWarehouseAcls','changePermissions','transferObjectOwnership',
                  'changePipelineAcls','changeFeatureTableAcl','addPrincipalToGroup','changeIamRoleAcl',
                  'changeInstancePoolAcl','changeJobAcl','resetJobAcl','changeRegisteredModelAcl',
                  'changeInferenceEndpointAcl','putAcl','changeSecurableOwner','grantPermission',
                  'changeWorkspaceAcl','updateRoleAssignment','setAccountAdmin','changeAccountOwner',
                  'updatePermissions','updateSharePermissions'
                )
              GROUP BY 1
            ),
            last_seen AS (
              SELECT
                lower(coalesce(user_identity.email, request_params.user)) AS email,
                max(event_time) AS last_event_time,
                datediff(current_date(), max(event_time)) AS days_since_last_event,
                count(distinct workspace_id) AS workspaces_seen_last_90d
              FROM system.access.audit
              WHERE event_date >= current_date() - INTERVAL 90 DAYS
              GROUP BY 1
            ),
            over_provisioning_signals AS (
              SELECT
                p.email,
                p.privilege_grants,
                coalesce(a.admin_actions_last_90d, 0) AS admin_actions_last_90d,
                l.days_since_last_event,
                l.workspaces_seen_last_90d,
                CASE WHEN p.privilege_grants >= 50 AND coalesce(a.admin_actions_last_90d, 0) = 0
                     THEN true ELSE false END AS has_high_privileges_no_admin_activity,
                CASE WHEN l.days_since_last_event >= 60 AND p.privilege_grants > 0
                     THEN true ELSE false END AS inactive_with_privileges,
                CASE WHEN p.privilege_grants >= 25 AND l.workspaces_seen_last_90d >= 3 AND coalesce(a.admin_actions_last_90d, 0) < 3
                     THEN true ELSE false END AS multi_workspace_low_activity
              FROM privileged_users p
              LEFT JOIN admin_like_activity a USING (email)
              LEFT JOIN last_seen l USING (email)
            ),
            over_provisioning_scores AS (
              SELECT
                *,
                (CASE WHEN has_high_privileges_no_admin_activity THEN 2 ELSE 0 END
                 + CASE WHEN inactive_with_privileges THEN 2 ELSE 0 END
                 + CASE WHEN multi_workspace_low_activity THEN 1 ELSE 0 END) AS over_provisioning_score
              FROM over_provisioning_signals
            )
            SELECT *
            FROM over_provisioning_scores
            WHERE over_provisioning_score > 0
            ORDER BY over_provisioning_score DESC, privilege_grants DESC
            LIMIT 100
            """
            result = await provider.execute_sql(query, timeout_seconds=300, obo_token=obo_token, require_obo=True)
            return {
                "risk_assessment": result.get("rows", []),
                "methodology": "Combines privilege counts with 90-day activity logs (ACL changes, last login, workspaces touched).",
                "check_type": "risk_score"
            }

        elif check_type == "grants_summary":
            query = """
            WITH catalog_privileges AS (
              SELECT grantee, 'catalog' AS securable_type, count(*) AS total
              FROM system.information_schema.catalog_privileges GROUP BY 1, 2
            ),
            external_location_privileges AS (
              SELECT grantee, 'external_location' AS securable_type, count(*) AS total
              FROM system.information_schema.external_location_privileges GROUP BY 1, 2
            ),
            metastore_privileges AS (
              SELECT grantee, 'metastore' AS securable_type, count(*) AS total
              FROM system.information_schema.metastore_privileges GROUP BY 1, 2
            ),
            routine_privileges AS (
              SELECT grantee, 'function' AS securable_type, count(*) AS total
              FROM system.information_schema.routine_privileges GROUP BY 1, 2
            ),
            schema_privileges AS (
              SELECT grantee, 'schema' AS securable_type, count(*) AS total
              FROM system.information_schema.schema_privileges GROUP BY 1, 2
            ),
            storage_credential_privileges AS (
              SELECT grantee, 'storage_credential' AS securable_type, count(*) AS total
              FROM system.information_schema.storage_credential_privileges GROUP BY 1, 2
            ),
            table_privileges AS (
              SELECT grantee, 'table' AS securable_type, count(*) AS total
              FROM system.information_schema.table_privileges GROUP BY 1, 2
            ),
            volume_privileges AS (
              SELECT grantee, 'volume' AS securable_type, count(*) AS total
              FROM system.information_schema.volume_privileges GROUP BY 1, 2
            )
            SELECT grantee, securable_type, SUM(total) AS number_of_grants
            FROM (
              SELECT * FROM catalog_privileges
              UNION ALL SELECT * FROM external_location_privileges
              UNION ALL SELECT * FROM metastore_privileges
              UNION ALL SELECT * FROM routine_privileges
              UNION ALL SELECT * FROM schema_privileges
              UNION ALL SELECT * FROM storage_credential_privileges
              UNION ALL SELECT * FROM table_privileges
              UNION ALL SELECT * FROM volume_privileges
            )
            GROUP BY 1, 2
            ORDER BY number_of_grants DESC
            LIMIT 100
            """
            result = await provider.execute_sql(query, timeout_seconds=600, obo_token=obo_token, require_obo=True)
            return {
                "grants_by_user": result.get("rows", []),
                "check_type": "grants_summary"
            }

        elif check_type == "workspace_admins":
            query = """
                SELECT gm.member_external_id as email, g.display_name as group_name
                FROM system.information_schema.group_members gm
                JOIN system.information_schema.groups g ON gm.group_external_id = g.group_external_id
                WHERE g.display_name = 'admins'
                LIMIT 100
            """
            result = await provider.execute_sql(query, timeout_seconds=600, obo_token=obo_token, require_obo=True)
            return {
                "workspace_admins": result.get("rows", []),
                "check_type": "workspace_admins"
            }

        return {"result": []}
            
    except Exception as e:
        raise RetryableError(f"Failed to check overprovisioning: {str(e)}")
