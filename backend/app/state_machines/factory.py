"""
Factory for creating state machine instances based on request type.
"""
from app.models.request import RequestType
from app.db.request import RequestModel
from sqlalchemy.orm import Session
from app.state_machines.base import BaseRequestStateMachine
from app.state_machines.workspace_provision.state_machine import WorkspaceProvisionStateMachine
from app.state_machines.data_access.state_machine import DataAccessStateMachine
from app.state_machines.service_principal.state_machine import ServicePrincipalStateMachine
from app.state_machines.workspace_access.state_machine import WorkspaceAccessStateMachine
from app.state_machines.platform_admin.state_machine import SimplePlatformAdminStateMachine
from app.state_machines.github_repo.state_machine import GithubRepoCreationStateMachine
from app.state_machines.project_onboarding.state_machine import ProjectOnboardingStateMachine
from app.state_machines.catalog_schema.state_machine import CreateCatalogSchemaStateMachine
from app.state_machines.experiments.state_machine import SimpleEmailStateMachine, CampaignStateMachine
import logging

logger = logging.getLogger(__name__)


def get_state_machine(request: RequestModel, db: Session) -> BaseRequestStateMachine:
    """Factory to return the appropriate state machine instance."""
    try:
        # Ensure we have a valid enum
        r_type = RequestType(request.type)
    except ValueError:
        logger.error(f"Invalid request type '{request.type}' for request {request.id}")
        raise ValueError(f"Invalid request type: {request.type}")
    
    # Mapping of RequestType to StateMachine class (or function that returns instance)
    SM_MAPPING = {
        RequestType.WORKSPACE_PROVISION: WorkspaceProvisionStateMachine,
        RequestType.CATALOG_SCHEMA_TABLE_ACCESS: DataAccessStateMachine,
        RequestType.BATCH_DATA_ACCESS: DataAccessStateMachine,
        RequestType.DATA_ACCESS_REQUEST: DataAccessStateMachine,
        RequestType.SERVICE_PRINCIPAL: ServicePrincipalStateMachine,
        RequestType.WORKSPACE_ACCESS: WorkspaceAccessStateMachine,
        RequestType.GITHUB_REPO_CREATION: GithubRepoCreationStateMachine,
        RequestType.CATALOG_SCHEMA_TABLE: CreateCatalogSchemaStateMachine,
        RequestType.MARKETPLACE_CERTIFICATION: SimplePlatformAdminStateMachine,
        RequestType.REST_API_ACCESS: SimplePlatformAdminStateMachine,
        RequestType.PROJECT_ONBOARDING: ProjectOnboardingStateMachine,
        RequestType.SIMPLE_EMAIL: SimpleEmailStateMachine,
        RequestType.CAMPAIGN: CampaignStateMachine,
    }

    if r_type in SM_MAPPING:
        return SM_MAPPING[r_type](request, db)

    # Special handling for ReportExecution (lazy import to avoid circular dep if needed, though arguably could just move imports to top)
    if r_type == RequestType.REPORT_EXECUTION:
        from app.state_machines.reporting.state_machine import ReportExecutionStateMachine
        return ReportExecutionStateMachine(request, db)

    # Lazy import for Enforcement Sentinel
    if r_type == RequestType.ENFORCEMENT_SENTINEL:
        from app.state_machines.enforcement_sentinel.state_machine import EnforcementSentinelStateMachine
        return EnforcementSentinelStateMachine(request, db)

    # Lazy import for Asset Deduplication
    if r_type == RequestType.ASSET_DEDUPLICATION:
        from app.state_machines.asset_deduplication.state_machine import AssetDeduplicationStateMachine
        return AssetDeduplicationStateMachine(request, db)

    # Lazy import for Allowlist Exception
    if r_type == RequestType.ALLOWLIST_EXCEPTION:
        from app.state_machines.allowlist_exception.state_machine import AllowlistExceptionStateMachine
        return AllowlistExceptionStateMachine(request, db)

    # Fallback / Default for others (implement specific ones as needed)
    raise ValueError(f"No state machine implemented for request type: {r_type}")

