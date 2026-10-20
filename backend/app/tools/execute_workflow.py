from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.db.session import get_db
from app.db.request import RequestModel
from datetime import datetime, timezone
import logging
import uuid
from app.state_machines.facts import add_fact

logger = logging.getLogger(__name__)

class ExecuteWorkflowInput(BaseModel):
    workflow_type: str = Field(..., description="The type of workflow to execute (e.g., project_onboarding)")
    parameters: Dict[str, Any] = Field(..., description="Key-value parameters required by the workflow")
    acknowledge_duplicates: bool = Field(
        False,
        description=(
            "Set true only after the user has seen the possible duplicate requests reported for "
            "this submission and explicitly asked to submit anyway."
        ),
    )


def _possible_duplicates(db, workflow_type: str, parameters: Dict[str, Any], kwargs: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Matches from the duplicate-request check, or [] when it's off or errors.

    Fails open: a broken check must not stop every agent submission.
    """
    from app.services import duplicate_requests

    if not duplicate_requests.is_enabled():
        return []
    try:
        result = duplicate_requests.find_duplicate_requests(
            db,
            workflow_type,
            parameters,
            requester_email=kwargs.get("_user_email"),
            roles=kwargs.get("_user_roles"),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Duplicate-request check failed for %s; submitting without it: %s", workflow_type, e)
        return []
    return result.get("possible_duplicates") or []

@tool(
    name="execute_workflow",
    description="Initiate a governed self-service workflow (e.g. 'workspace_access', 'workspace_provision') with validated user parameters.",
    args_schema=ExecuteWorkflowInput,
    # Gateway to every provisioning workflow (catalog/schema/volume/SP/repo,
    # Terraform apply, UC grants, identity-group membership). The downstream
    # blast radius depends on `workflow_type`; classified `infra` as the
    # broadest bound. The workflow's own HITL approvals still apply.
    side_effect_class="infra",
)
def execute_workflow(
    workflow_type: str,
    parameters: Dict[str, Any],
    conversation_history: Optional[list] = None,
    acknowledge_duplicates: bool = False,
    **kwargs,
) -> Dict[str, Any]:
    """
    Create a request and trigger the workflow state machine.

    Holds the submission back when it looks like a duplicate of an open or recently
    finished request, until the agent re-calls with ``acknowledge_duplicates``.
    Only agent/MCP submissions come through here; form and API submissions use
    ``RequestService.create_request``, and sandboxed test runs never execute
    mutating tools.
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
        from app.workflows.graphs import published_graph_spec
        from app.workflows.graphs.specs import SPECS
        from app.workflows.instructions import filed_by

        filer = filed_by(published_graph_spec(db, workflow_type) or SPECS.get(workflow_type))
        if filer:
            return {
                "success": False,
                "error": f"'{workflow_type}' requests are filed by `{filer}`, not execute_workflow. Call `{filer}`.",
            }

        duplicates = _possible_duplicates(db, workflow_type, parameters, kwargs)
        if duplicates and not acknowledge_duplicates:
            # No ``success: False``: the ToolExecutor would recast the hold as a failed
            # call, showing a failure in chat and auditing an error for a correct pause.
            return {
                "submitted": False,
                "status": "possible_duplicates",
                "workflow_type": workflow_type,
                "possible_duplicates": duplicates,
                "message": (
                    f"Not submitted: {len(duplicates)} existing request(s) look like duplicates. "
                    "Show the user these matches and ask whether to submit anyway. Only if they "
                    "say yes, call execute_workflow again with the same arguments plus "
                    "acknowledge_duplicates=true."
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
        if duplicates:
            # Entries are already redacted for the caller, and the requester can
            # read their own request's facts, so nothing here exposes another
            # user's request.
            add_fact(
                db,
                request_id,
                "possible_duplicates_acknowledged",
                {
                    "matches": [
                        {k: m.get(k) for k in ("request_id", "status", "submitted_by_you", "matched_fields")}
                        for m in duplicates
                    ]
                },
                actor=user_email or "agent",
            )
        
        db.commit()
        
        return {
            "success": True,
            "workflow_type": workflow_type,
            "request_id": request_id,
            "request_link": f"[{request_id}](/requests/{request_id})",
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
