"""
Simple Platform Admin state machine.
Generic state machine for requests requiring only Platform Admin approval.
"""
from statemachine import State
from app.models.request import RequestType
from app.state_machines.decorators import workflow
from app.state_machines.base import BaseRequestStateMachine


@workflow(request_types=[RequestType.MARKETPLACE_CERTIFICATION, RequestType.REST_API_ACCESS], feature_flag="core")
class SimplePlatformAdminStateMachine(BaseRequestStateMachine):
    """Generic state machine for requests requiring only Platform Admin approval."""
    
    pending = State("pending", initial=True)
    platform_admin_approval = State("platform_admin_approval")
    provisioning = State("provisioning")
    completed = State("completed", final=True)
    rejected = State("rejected", final=True)

    submit = pending.to(platform_admin_approval, cond="has_request_submitted")
    approve_admin = platform_admin_approval.to(provisioning, cond="has_platform_admin_approval")
    finish_provisioning = provisioning.to(completed, cond="has_workspace_created")
    reject = (
        pending.to(rejected, cond="has_request_rejected") |
        platform_admin_approval.to(rejected, cond="has_request_rejected")
    )
    
    # Approval node configuration
    APPROVAL_NODES = {
        "platform_admin_approval": {"approval_type": "platform_admin", "name": "Platform Admin Approval"}
    }
