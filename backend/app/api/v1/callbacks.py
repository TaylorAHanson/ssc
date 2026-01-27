"""
Terraform Callback API endpoints.
"""
from fastapi import APIRouter, HTTPException, Depends, status
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.services.request_service import RequestService
from app.state_machines.facts import add_fact
from pydantic import BaseModel
from typing import Optional, Dict, Any, Literal
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

router = APIRouter()

class TerraformCallback(BaseModel):
    action: Literal["plan", "apply"]
    status: Literal["success", "failure"]
    summary: str
    outputs: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

@router.post("/{request_id}", status_code=status.HTTP_200_OK)
async def terraform_callback(
    request_id: str,
    payload: TerraformCallback,
    db: Session = Depends(get_db)
):
    """
    Handle callbacks from Terraform CI/CD pipelines.
    
    This endpoint is called by the CI/CD system (e.g. GitHub Actions) 
    after 'terraform plan' or 'terraform apply' completes.
    
    It records a fact (terraform_plan_received or terraform_apply_received) 
    which the State Machine uses to transition states.
    """
    logger.info(f"Received terraform callback for {request_id}: {payload.action} {payload.status}")
    
    request = RequestService.get_request(db, request_id)
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    
    if payload.action == "plan":
        fact_name = "terraform_plan_received"
    elif payload.action == "apply":
        fact_name = "terraform_apply_received"
    else:
        raise HTTPException(status_code=400, detail=f"Invalid action: {payload.action}")
        
    fact_data = {
        "status": payload.status,
        "summary": payload.summary,
        "outputs": payload.outputs,
        "error": payload.error,
        "received_at": datetime.utcnow().isoformat()
    }
    
    add_fact(db, request_id, fact_name, fact_data, actor="system-cicd")
    db.commit()
    
    return {"status": "ok", "message": f"{payload.action} result recorded"}
