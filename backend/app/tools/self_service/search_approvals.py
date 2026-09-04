"""
Tool to search for pending approvals.
"""
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.db.session import get_db
from app.db import ApprovalModel, RequestModel
from app.services.approval_scope import approval_visibility_filter, parse_csv

class SearchApprovalsInput(BaseModel):
    approval_type: Optional[str] = Field(None, description="Filter by approval type: 'manager', 'data_owner', or 'platform_admin' (optional)")
    status: str = Field("pending", description="Filter by approval status (default: 'pending') (optional)")
    request_id: Optional[str] = Field(None, description="Filter by a specific request ID (optional)")
    limit: int = Field(5, description="Maximum number of results to return (default: 5)")

@tool(
    name="search_approvals",
    description="List pending approval tasks awaiting review, filterable by approval type ('manager', 'data_owner', 'platform_admin') or request ID.",
    args_schema=SearchApprovalsInput
)
def search_approvals(
    approval_type: Optional[str] = None, 
    status: str = "pending", 
    request_id: Optional[str] = None, 
    limit: int = 5,
    _user_email: Optional[str] = None,
    _user_roles: Optional[str] = None,
    _user_entitlements: Optional[str] = None
) -> Dict[str, Any]:
    """
    Execute the search for approvals.
    """
    # Fail closed. Without a caller identity there is no way to scope this to the
    # approvals they may see, and the previous behavior — skip the filter — meant
    # an unidentified caller received *every* approval in the system, complete
    # with other users' request titles and requester addresses.
    if not _user_email:
        return {
            "status": "error",
            "error": "Cannot search approvals without a caller identity.",
        }

    db = next(get_db())
    try:
        # Join with RequestModel to get titles
        sql_query = db.query(ApprovalModel, RequestModel).join(
            RequestModel, ApprovalModel.request_id == RequestModel.id
        )

        # Always scoped. Absent roles/entitlements this narrows to the approvals
        # assigned or delegated to the caller personally, rather than widening to
        # everyone's.
        sql_query = sql_query.filter(
            approval_visibility_filter(
                _user_email,
                roles=parse_csv(_user_roles),
                entitlements=parse_csv(_user_entitlements),
            )
        )


        # Filter by status
        if status:
            sql_query = sql_query.filter(ApprovalModel.status == status.lower())
        
        # Filter by approval type
        if approval_type:
            sql_query = sql_query.filter(ApprovalModel.approval_type == approval_type.lower())
            
        # Filter by request ID
        if request_id:
            sql_query = sql_query.filter(ApprovalModel.request_id == request_id)
        
        # Order by newest first
        sql_query = sql_query.order_by(ApprovalModel.created_at.desc())
        
        # Limit
        results = sql_query.limit(limit).all()
        
        approvals_list = []
        for approval, request in results:
            approvals_list.append({
                "approval_id": approval.id,
                "request_id": approval.request_id,
                "request_title": request.title,
                "request_type": request.type,
                "approval_type": approval.approval_type,
                "status": approval.status,
                "requested_by": approval.requested_by,
                "created_at": approval.created_at.isoformat()
            })
        
        return {
            "count": len(approvals_list),
            "approvals": approvals_list
        }
        
    finally:
        db.close()
