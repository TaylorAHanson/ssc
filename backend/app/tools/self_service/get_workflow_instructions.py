"""
Tool to retrieve specific instructions for a workflow.
"""
from typing import Dict, Any
from pydantic import BaseModel, Field
import os
import re
from app.tools.mcp import tool
from app.core.exceptions import RetryableError

class GetWorkflowInstructionsInput(BaseModel):
    workflow_name: str = Field(..., description="The internal name of the workflow to retrieve instructions for, e.g. 'create_catalog_schema'")

@tool(
    name="get_workflow_instructions",
    description="Retrieves the detailed, step-by-step instructions for executing a specific workflow. You MUST use this tool to learn how to gather information and format the execution request whenever a user asks to perform a workflow listed in your Capabilities.",
    args_schema=GetWorkflowInstructionsInput
)
async def get_workflow_instructions(workflow_name: str) -> Dict[str, Any]:
    """
    Fetch the markdown instructions for a specific workflow.
    """
    try:
        # Sanitize workflow name just in case
        clean_name = re.sub(r'[^a-zA-Z0-9_]', '', workflow_name.replace('.md', ''))
        filename = f"{clean_name}.md"
        
        # Path to instructions directory relative to this file
        instructions_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "agents", "instructions")
        filepath = os.path.join(instructions_dir, filename)
        
        if not os.path.exists(filepath):
            # Try to find a partial match
            available_files = [f.replace('.md', '') for f in os.listdir(instructions_dir) if f.endswith('.md')]
            matches = [f for f in available_files if clean_name.lower() in f.lower()]
            
            if matches:
                return {
                    "error": f"Workflow '{workflow_name}' not found. Did you mean one of these: {', '.join(matches)}?",
                    "found": False
                }
            return {
                "error": f"Workflow '{workflow_name}' not found. Available workflows: {', '.join(available_files)}",
                "found": False
            }
            
        with open(filepath, "r") as f:
            content = f.read()
            
        return {
            "workflow": clean_name,
            "instructions": content,
            "found": True
        }
        
    except Exception as e:
        raise RetryableError(f"Failed to fetch workflow instructions: {str(e)}")