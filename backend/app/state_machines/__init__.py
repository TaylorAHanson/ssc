"""
State machine definitions using python-statemachine.

State machines are organized in separate files:
- base.py - BaseRequestStateMachine (base class)
- factory.py - get_state_machine() factory function
- Individual state machine implementations in separate files
"""
from app.state_machines.base import BaseRequestStateMachine
from app.state_machines.factory import get_state_machine
from app.state_machines.workspace_provision.state_machine import WorkspaceProvisionStateMachine
from app.state_machines.data_access.state_machine import DataAccessStateMachine
from app.state_machines.service_principal.state_machine import ServicePrincipalStateMachine
from app.state_machines.workspace_access.state_machine import WorkspaceAccessStateMachine
from app.state_machines.platform_admin.state_machine import SimplePlatformAdminStateMachine
from app.state_machines.github_repo.state_machine import GithubRepoCreationStateMachine
from app.state_machines.project_onboarding.state_machine import ProjectOnboardingStateMachine
from app.state_machines.catalog_schema.state_machine import CreateCatalogSchemaStateMachine
from app.state_machines.enforcement_sentinel.state_machine import EnforcementSentinelStateMachine

__all__ = [
    "BaseRequestStateMachine",
    "get_state_machine",
    "WorkspaceProvisionStateMachine",
    "DataAccessStateMachine",
    "ServicePrincipalStateMachine",
    "WorkspaceAccessStateMachine",
    "SimplePlatformAdminStateMachine",
    "GithubRepoCreationStateMachine",
    "ProjectOnboardingStateMachine",
    "CreateCatalogSchemaStateMachine",
    "EnforcementSentinelStateMachine",
]
