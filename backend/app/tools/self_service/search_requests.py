"""
Tool to search for requests.
"""
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.db.session import get_db
from app.db.request import RequestModel
from sqlalchemy import or_

class SearchRequestsInput(BaseModel):
    query: Optional[str] = Field(None, description="Search term to match against request title or ID (optional)")
    status: Optional[str] = Field(None, description="Filter by request status (e.g., 'pending', 'completed', 'failed') (optional)")
    limit: int = Field(5, description="Maximum number of results to return (default: 5)")

@tool(
    name="search_requests",
    description="Search for existing requests by title, ID, or status. Use this to check the status of a user's request or find specific requests.",
    args_schema=SearchRequestsInput
)
async def search_requests(
    query: Optional[str] = None, 
    status: Optional[str] = None, 
    limit: int = 5,
    _user_email: Optional[str] = None,
    _user_roles: Optional[str] = None
) -> Dict[str, Any]:
    """
    Execute the search.
    """
    db = next(get_db())
    try:
        sql_query = db.query(RequestModel)
        
        # Apply role-based filtering
        if _user_email and _user_roles is not None:
            roles_list = [r.strip().lower() for r in _user_roles.split(",")]
            if "platform admin" not in roles_list:
                sql_query = sql_query.filter(RequestModel.requester_email == _user_email)
        
        # Filter by text query
        if query:
            search_term = f"%{query}%"
            sql_query = sql_query.filter(
                or_(
                    RequestModel.title.ilike(search_term),
                    RequestModel.id.ilike(search_term)
                )
            )
        
        # Filter by status
        if status:
            # normalize status
            status_lower = status.lower()
            sql_query = sql_query.filter(RequestModel.status == status_lower)
        
        # Order by newest first
        sql_query = sql_query.order_by(RequestModel.created_at.desc())
        
        # Limit
        results = sql_query.limit(limit).all()
        
        requests_list = []
        for req in results:
            requests_list.append({
                "id": req.id,
                "title": req.title,
                "type": req.type,
                "status": req.status,
                "current_state": req.current_state,
                "created_at": req.created_at.isoformat(),
                "updated_at": req.updated_at.isoformat()
            })
        
        return {
            "count": len(requests_list),
            "requests": requests_list
        }
        
    finally:
        db.close()
