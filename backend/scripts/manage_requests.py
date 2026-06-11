"""
Developer CLI script for managing request data (clearing and seeding).
Usage:
    python scripts/manage_requests.py clear
    python scripts/manage_requests.py seed
"""
import os
import sys
import random
import asyncio
import logging
from datetime import datetime

# Add the backend directory to sys.path to allow imports from 'app'
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import Session
from app.db.session import get_lakebase_session
from app.db import RequestModel, ApprovalModel, EventModel, FailureModel
from app.models.request import RequestCreate, Environment
from app.services.request_service import RequestService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def clear_data(db: Session):
    """Delete all request-related data."""
    try:
        print("Clearing all request data...")
        db.query(FailureModel).delete()
        db.query(EventModel).delete()
        db.query(ApprovalModel).delete()
        db.query(RequestModel).delete()
        db.commit()
        print("Success: All request data has been cleared.")
    except Exception as e:
        db.rollback()
        print(f"Error clearing data: {e}")
        sys.exit(1)

async def seed_data(db: Session):
    """Generate 10 mock requests."""
    print("Seeding 10 mock requests...")
    # Request types are data-driven strings (validated against the registry).
    types = [
        ("workspace_provision", "New Data Lab: Project Phoenix", Environment.DEV),
        ("data_access_request", "Access to Sales Leads 2024", Environment.PROD),
        ("github_repo_creation", "Repository for ML-Pipeline-V2", Environment.TEST),
        ("service_principal", "Service Principal for ADF Integration", Environment.STAGE),
        ("project_onboarding", "Team Onboarding: Finance Analytics", Environment.DEV),
        ("workspace_access", "Access to Enterprise-Dev Workspace", Environment.DEV),
        ("catalog_schema_table", "Create Gold Catalog for Marketing", Environment.PROD),
        ("workspace_provision", "Sandbox for Spark Experiments", Environment.TEST),
        ("data_access_request", "PII Data Access for Audit", Environment.PROD),
        ("github_repo_creation", "New Repo for Infra-as-Code", Environment.STAGE),
    ]
    
    users = ["alice.smith@example.com", "bob.jones@example.com", "carol.white@example.com"]
    
    created_count = 0
    try:
        for req_type, title, env in types:
            user_email = random.choice(users)
            user_name = user_email.split("@")[0].replace(".", " ").title()
            
            request_data = RequestCreate(
                type=req_type,
                title=title,
                environment=env,
                metadata={
                    "requested_by": user_name,
                    "requested_by_email": user_email,
                    "justification": "Required for scheduled project deliverables.",
                    "team": random.choice(["Enterprise Data", "Finance", "Sales", "Supply Chain"])
                },
                conversation=[
                    {"role": "user", "content": f"I need to create a {req_type} for my team."},
                    {"role": "assistant", "content": f"I can help with that. I'll need some details like the title and environment."},
                    {"role": "user", "content": f"The title is '{title}' and it's for {env.value}."}
                ]
            )
            
            RequestService.create_request(db, request_data)
            created_count += 1
        
        db.commit()
        print(f"Success: Seeded {created_count} requests.")
    except Exception as e:
        db.rollback()
        print(f"Error seeding data: {e}")
        sys.exit(1)

async def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/manage_requests.py [clear|seed]")
        sys.exit(1)
    
    action = sys.argv[1]
    db = get_lakebase_session()
    try:
        if action == "clear":
            clear_data(db)
        elif action == "seed":
            await seed_data(db)
        else:
            print(f"Invalid action: {action}")
            sys.exit(1)
    finally:
        db.close()

if __name__ == "__main__":
    asyncio.run(main())
