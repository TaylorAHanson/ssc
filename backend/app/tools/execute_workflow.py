from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.db.session import get_db
from app.db.request import RequestModel
from datetime import datetime, timezone
import uuid
from app.state_machines.facts import add_fact

class ExecuteWorkflowInput(BaseModel):
    workflow_type: str = Field(..., description="The type of workflow to execute (e.g., project_onboarding)")
    parameters: Dict[str, Any] = Field(..., description="Key-value parameters required by the workflow")

@tool(
    name="execute_workflow",
    description="Initiate a governed self-service workflow (e.g. 'request_data_access', 'workspace_provision') with validated user parameters.",
    args_schema=ExecuteWorkflowInput,
    # Gateway to every provisioning workflow (catalog/schema/volume/SP/repo,
    # Terraform apply, UC grants, identity-group membership). The downstream
    # blast radius depends on `workflow_type`; classified `infra` as the
    # broadest bound. The workflow's own HITL approvals still apply.
    side_effect_class="infra",
)
def execute_workflow(workflow_type: str, parameters: Dict[str, Any], conversation_history: Optional[list] = None, **kwargs) -> Dict[str, Any]:
    """
    Create a request and trigger the workflow state machine.
    """
    # Get user email from injected context
    user_email = kwargs.get("_user_email")
    
    db = next(get_db())
    try:
        # Request types are data-driven: reject types that aren't a published
        # workflow (or bundled default) so the agent must author + publish first.
        from app.services.workflow_service import WorkflowService
        if not WorkflowService.is_known_request_type(db, workflow_type):
            return {
                "success": False,
                "error": (
                    f"Unknown workflow_type '{workflow_type}'. No published workflow "
                    f"governs this type yet — author and publish one first."
                ),
            }

        # Generate Request ID
        request_id = f"req-{str(uuid.uuid4())}"
        
        # Create Request
        # Add user email to state_context for state machine usage (e.g., granting access)
        state_context_with_email = {
            **parameters,
            "requested_by_email": user_email
        }

        request = RequestModel(
            id=request_id,
            type=workflow_type,
            title=f"Agent Request: {workflow_type}",
            status="pending",
            current_state="pending",
            state_context=state_context_with_email,
            conversation=conversation_history,  # Save chat history
            requester_email=user_email,  # Set requester for permission filtering
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        
        db.add(request)
        
        # Add 'request_submitted' fact to trigger the state machine immediately
        add_fact(db, request_id, "request_submitted", {}, actor="agent")
        
        db.commit()
        
        return {
            "success": True,
            "workflow_type": workflow_type,
            "request_id": request_id,
            "status": "initiated",
            "message": f"Successfully initiated {workflow_type} workflow with ID {request_id}"
        }
    except Exception as e:
        db.rollback()
        return {
            "success": False,
            "error": str(e)
        }
    finally:
        db.close()
