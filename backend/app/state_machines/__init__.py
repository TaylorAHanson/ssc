"""
State machine definitions using python-statemachine.

State machines are organized in separate files:
- base.py - BaseRequestStateMachine (base class)
- factory.py - get_state_machine() factory function
- Individual state machine implementations in separate files
"""
from app.state_machines.base import BaseRequestStateMachine
from app.state_machines.factory import get_state_machine
from app.state_machines.workspace_provision import WorkspaceProvisionStateMachine
from app.state_machines.data_access import DataAccessStateMachine
from app.state_machines.service_principal import ServicePrincipalStateMachine
from app.state_machines.workspace_access import WorkspaceAccessStateMachine
from app.state_machines.simple_platform_admin import SimplePlatformAdminStateMachine
from app.state_machines.github_repo_creation import GithubRepoCreationStateMachine

__all__ = [
    "BaseRequestStateMachine",
    "get_state_machine",
    "WorkspaceProvisionStateMachine",
    "DataAccessStateMachine",
    "ServicePrincipalStateMachine",
    "WorkspaceAccessStateMachine",
    "SimplePlatformAdminStateMachine",
    "GithubRepoCreationStateMachine",
]
