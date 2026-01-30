from typing import Dict, Any, Type
from pydantic import BaseModel, Field
from app.tools.base import BaseTool
from app.models.request import RequestType
from app.state_machines.factory import get_state_machine
from app.db.session import get_lakebase_session
from app.db.request import RequestModel
from datetime import datetime
import uuid

class ExecuteWorkflowInput(BaseModel):
    workflow_type: str = Field(..., description="The type of workflow to execute (e.g., project_onboarding)")
    parameters: Dict[str, Any] = Field(..., description="Key-value parameters required by the workflow")

class ExecuteWorkflowTool(BaseTool):
    name: str = "execute_workflow"
    description: str = "Executes a specified workflow with the given parameters and initiates the corresponding state machine."
    input_schema: Type[BaseModel] = ExecuteWorkflowInput

    async def execute(self, workflow_type: str, parameters: Dict[str, Any], conversation_history: list = None, **kwargs) -> Dict[str, Any]:
        """
        Create a request and trigger the workflow state machine.
        """
        # Get user email from injected context
        user_email = kwargs.get("_user_email")
        
        db = get_lakebase_session()
        try:
            # Generate Request ID
            request_id = f"req-{str(uuid.uuid4())}"
            
            # Map string type to enum if possible, or pass raw string
            # In a real app, we'd validate against RequestType enum
            
            # Create Request
            request = RequestModel(
                id=request_id,
                type=workflow_type,
                title=f"Agent Request: {workflow_type}",
                status="pending",
                current_state="pending",
                state_context=parameters,
                conversation=conversation_history,  # Save chat history
                requester_email=user_email,  # Set requester for permission filtering
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            
            db.add(request)
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
