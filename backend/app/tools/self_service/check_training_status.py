from typing import List, Any
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.providers.training.client import TrainingProvider
from app.db.session import get_lakebase_session

class CheckTrainingStatusArgs(BaseModel):
    user_email: str = Field(None, description="Email of the user (Required for 'status' query, ignored for others)")
    query_type: str = Field("status", description="Type of query: 'status' (default), 'leaderboard', or 'recent'")

@tool(
    name="check_training_status",
    description="Check training status. Use 'status' to check a user's completed courses. Use 'leaderboard' for top users (requires no email). Use 'recent' for last 7 days completions (requires no email).",
    args_schema=CheckTrainingStatusArgs
)
def check_training_status(user_email: str = None, query_type: str = "status") -> Any:
    db = get_lakebase_session()
    try:
        provider = TrainingProvider(db)
        
        if query_type == "leaderboard":
            return provider.get_training_leaderboard()
            
        if query_type == "recent":
            return provider.get_recent_completions()
            
        # Default to status check
        if not user_email:
            return "Please provide an email address to check status."
            
        return provider.get_user_training_status(user_email)
    finally:
        db.close()
