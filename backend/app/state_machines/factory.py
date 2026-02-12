"""
Factory for creating state machine instances based on request type.
"""
from app.models.request import RequestType
from app.db.request import RequestModel
from sqlalchemy.orm import Session
from app.state_machines.base import BaseRequestStateMachine
from app.state_machines.workspace_provision import WorkspaceProvisionStateMachine
from app.state_machines.data_access import DataAccessStateMachine
from app.state_machines.service_principal import ServicePrincipalStateMachine
from app.state_machines.workspace_access import WorkspaceAccessStateMachine
from app.state_machines.simple_platform_admin import SimplePlatformAdminStateMachine
from app.state_machines.github_repo_creation import GithubRepoCreationStateMachine
from app.state_machines.project_onboarding import ProjectOnboardingStateMachine
from app.state_machines.project_onboarding import ProjectOnboardingStateMachine
from app.state_machines.create_catalog_schema import CreateCatalogSchemaStateMachine
from app.state_machines.experiments import SimpleEmailStateMachine, CampaignStateMachine
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
    
    if r_type == RequestType.WORKSPACE_PROVISION:
        return WorkspaceProvisionStateMachine(request, db)
    
    elif r_type in [RequestType.CATALOG_SCHEMA_TABLE_ACCESS, RequestType.BATCH_DATA_ACCESS, RequestType.DATA_ACCESS_REQUEST]:
        return DataAccessStateMachine(request, db)
        
    elif r_type == RequestType.SERVICE_PRINCIPAL:
        return ServicePrincipalStateMachine(request, db)

    elif r_type == RequestType.WORKSPACE_ACCESS:
        return WorkspaceAccessStateMachine(request, db)

    elif r_type == RequestType.GITHUB_REPO_CREATION:
        return GithubRepoCreationStateMachine(request, db)

    elif r_type == RequestType.CATALOG_SCHEMA_TABLE:
        return CreateCatalogSchemaStateMachine(request, db)

    elif r_type in [RequestType.MARKETPLACE_CERTIFICATION, RequestType.REST_API_ACCESS]:
        # Use SimplePlatformAdminStateMachine for these for now
        return SimplePlatformAdminStateMachine(request, db)
    
    elif r_type == RequestType.PROJECT_ONBOARDING:
        return ProjectOnboardingStateMachine(request, db)

    # This is for testing the coupmound workflows
    elif r_type == RequestType.SIMPLE_EMAIL:
        return SimpleEmailStateMachine(request, db)

    # This is for testing the coupmound workflows
    elif r_type == RequestType.CAMPAIGN:
        return CampaignStateMachine(request, db)

    elif r_type == RequestType.REPORT_EXECUTION:
        from app.state_machines.report_execution import ReportExecutionStateMachine
        return ReportExecutionStateMachine(request, db)

    # Fallback / Default for others (implement specific ones as needed)
    raise ValueError(f"No state machine implemented for request type: {r_type}")

