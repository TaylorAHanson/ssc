"""
Tool to search for pending approvals.
"""
from typing import Dict, Any, List, Optional
from app.tools.base import BaseTool
from app.db.session import get_lakebase_session
from app.db.request import ApprovalModel, RequestModel
from sqlalchemy import or_

class SearchApprovalsTool(BaseTool):
    """Tool to search for pending approvals."""
    
    @property
    def name(self) -> str:
        return "search_approvals"

    @property
    def description(self) -> str:
        return "Search for pending approvals by type (manager, data_owner, platform_admin) or status. Use this to find requests that are awaiting action from specific roles."

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "approval_type": {
                    "type": "string",
                    "description": "Filter by approval type: 'manager', 'data_owner', or 'platform_admin' (optional)"
                },
                "status": {
                    "type": "string",
                    "description": "Filter by approval status (default: 'pending') (optional)",
                    "default": "pending"
                },
                "request_id": {
                    "type": "string",
                    "description": "Filter by a specific request ID (optional)"
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default: 5)"
                }
            },
            "required": []
        }

    async def execute(self, approval_type: str = None, status: str = "pending", request_id: str = None, limit: int = 5) -> Dict[str, Any]:
        """
        Execute the search for approvals.
        """
        db = get_lakebase_session()
        try:
            # Join with RequestModel to get titles
            sql_query = db.query(ApprovalModel, RequestModel).join(
                RequestModel, ApprovalModel.request_id == RequestModel.id
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
