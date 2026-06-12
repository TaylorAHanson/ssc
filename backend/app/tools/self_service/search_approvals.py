"""
Tool to search for pending approvals.
"""
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.db.session import get_db
from app.db import ApprovalModel, RequestModel
from sqlalchemy import or_

class SearchApprovalsInput(BaseModel):
    approval_type: Optional[str] = Field(None, description="Filter by approval type: 'manager', 'data_owner', or 'platform_admin' (optional)")
    status: str = Field("pending", description="Filter by approval status (default: 'pending') (optional)")
    request_id: Optional[str] = Field(None, description="Filter by a specific request ID (optional)")
    limit: int = Field(5, description="Maximum number of results to return (default: 5)")

@tool(
    name="search_approvals",
    description="Search for pending approvals by type (manager, data_owner, platform_admin) or status. Use this to find requests that are awaiting action from specific roles.",
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
    db = next(get_db())
    try:
        # Join with RequestModel to get titles
        sql_query = db.query(ApprovalModel, RequestModel).join(
            RequestModel, ApprovalModel.request_id == RequestModel.id
        )
        
        # Apply role-based filtering
        if _user_email and _user_roles is not None:
            roles_list = [r.strip().lower() for r in _user_roles.split(",")]
            entitlements_list = [e.strip() for e in _user_entitlements.split(",")] if _user_entitlements else []
            
            allowed_types = []
            if "platform admin" in roles_list:
                allowed_types.extend(["platform_admin", "manager", "data_owner", "security", "security_admin", "finance_admin", "governance_admin"])
            if "governance admin" in roles_list:
                allowed_types.append("governance_admin")
            if "security admin" in roles_list:
                allowed_types.extend(["security", "security_admin"])
            if "finance admin" in roles_list:
                allowed_types.append("finance_admin")
                
            sql_query = sql_query.filter(
                (ApprovalModel.assigned_to_email == _user_email) | 
                (ApprovalModel.delegated_to_email == _user_email) |
                (ApprovalModel.assigned_to_role.in_(entitlements_list)) |
                (ApprovalModel.approval_type.in_(allowed_types))
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
