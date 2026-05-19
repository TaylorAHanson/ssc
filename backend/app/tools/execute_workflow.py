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
    description="Executes a specified workflow with the given parameters and initiates the corresponding state machine.",
    args_schema=ExecuteWorkflowInput
)
async def execute_workflow(workflow_type: str, parameters: Dict[str, Any], conversation_history: Optional[list] = None, **kwargs) -> Dict[str, Any]:
    """
    Create a request and trigger the workflow state machine.
    """
    # Get user email from injected context
    user_email = kwargs.get("_user_email")
    
    db = next(get_db())
    try:
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
