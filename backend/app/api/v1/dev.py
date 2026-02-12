from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List
import subprocess
import sys
import os
import logging
from app.api.deps import require_role

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_role("platform_admin"))])

class TestRunRequest(BaseModel):
    path: Optional[str] = None
    args: Optional[List[str]] = None

class TestRunResponse(BaseModel):
    exit_code: int
    stdout: str
    stderr: str
    command: List[str]

@router.post("/tests", response_model=TestRunResponse)
def run_tests(request: TestRunRequest):
    """Run pytest with optional path and arguments."""
    logger.info(f"Running tests for path: {request.path}")
    
    # Security check: Ensure path is safe (simple check)
    if request.path and (".." in request.path or request.path.startswith("/")):
        raise HTTPException(status_code=400, detail="Invalid test path")
    
    # Determine the root directory of the project
    # This assumes dev.py is in backend/app/api/dev.py
    cwd = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    
    # Construct command
    command = [sys.executable, "-m", "pytest"]
    if request.path:
        command.append(request.path)
    if request.args:
        command.extend(request.args)
    
    try:
        # Set PYTHONPATH to include the project root
        env = os.environ.copy()
        env["PYTHONPATH"] = cwd
        
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            env=env
        )
        
        return {
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "command": command
        }
    except Exception as e:
        logger.error(f"Failed to run tests: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tests/list")
def list_tests():
    """List available tests using pytest --collect-only."""
    # Determine the root directory of the project
    cwd = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    
    command = [sys.executable, "-m", "pytest", "--collect-only", "-q"]
    
    try:
        env = os.environ.copy()
        env["PYTHONPATH"] = cwd
        
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            env=env
        )
        
        # Parse output to extract test IDs
        tests = []
        for line in result.stdout.splitlines():
            if "::" in line and not line.startswith("no tests ran"):
                tests.append(line.strip())
                
        return {"tests": tests}
    except Exception as e:
        logger.error(f"Failed to list tests: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/db/reset")
def reset_db():
    """Reset the database (DROP ALL TABLES)."""
    try:
        # Import all models to ensure they are registered with Base
        from app.db.base import Base
        from app.db import RequestModel, ApprovalModel, EventModel, FailureModel, DelegationModel
        from app.db.session import get_engine
        
        engine = get_engine()
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        
        return {"message": "Database reset successfully"}
    except Exception as e:
        logger.error(f"Failed to reset database: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/db/seed")
def seed_db():
    """Seed the database with initial data."""
    try:
        from app.db.session import get_session_local
        from app.db import RequestModel
        from app.models.request import RequestType, RequestStatus
        from datetime import datetime, timedelta
        import uuid
        
        db = get_session_local()()
        try:
            # Seed Roles
            from app.db.user import RoleModel, UserModel
            
            roles = [
                {"id": "role_platform_admin", "name": "platform_admin", "description": "Full system access"},
                {"id": "role_governance_admin", "name": "governance_admin", "description": "Governance and policy management"},
                {"id": "role_security_admin", "name": "security_admin", "description": "Security auditing and access control"},
                {"id": "role_finance_admin", "name": "finance_admin", "description": "Budget and cost management"},
            ]
            
            for role_data in roles:
                if not db.query(RoleModel).filter(RoleModel.name == role_data["name"]).first():
                    role = RoleModel(**role_data)
                    db.add(role)
            
            db.commit()

            # Seed Admin User
            admin_email = "admin@qualcomm.com"
            admin_user = db.query(UserModel).filter(UserModel.email == admin_email).first()
            
            if not admin_user:
                admin_user = UserModel(
                    id=str(uuid.uuid4()),
                    email=admin_email,
                    full_name="System Admin",
                    is_active=True
                )
                db.add(admin_user)
            
            # Always ensure admin has all roles
            all_roles = db.query(RoleModel).all()
            # Convert list of roles to set of IDs to avoid duplicates if appending, 
            # but for admin we can just reset the relationship
            admin_user.roles = all_roles
            
            db.commit()

            # Create sample requests if none exist
            if db.query(RequestModel).count() == 0:
                requests = [
                    RequestModel(
                        id=str(uuid.uuid4()),
                        title="Sample Project Onboarding",
                        type="project_onboarding",
                        status="pending",
                        created_at=datetime.utcnow(),
                        updated_at=datetime.utcnow()
                    ),
                    RequestModel(
                        id=str(uuid.uuid4()),
                        title="Access to Finance Data",
                        type="data_access_request",
                        status="manager_approval",
                        created_at=datetime.utcnow() - timedelta(days=1),
                        updated_at=datetime.utcnow()
                    ),
                    RequestModel(
                        id=str(uuid.uuid4()),
                        title="New Service Principal",
                        type="service_principal",
                        status="completed",
                        created_at=datetime.utcnow() - timedelta(days=5),
                        updated_at=datetime.utcnow() - timedelta(days=4)
                    )
                ]
                
                for req in requests:
                    db.add(req)
                
                db.commit()
                return {"message": f"Seeded roles, admin user, and {len(requests)} requests"}
            else:
                return {"message": "Database seeded with roles and admin user (requests already existed)"}
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Failed to seed database: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/github/setup-templates")
async def setup_github_templates():
    """Seed the GitHub organization with test templates."""
    try:
        from app.core.config import settings
        from app.providers.github.client import GitHubProvider
        import httpx
        from datetime import datetime
        import base64

        token = settings.GITHUB_TOKEN or settings.get_git_token()
        org = settings.GITHUB_ORG
        
        if not token or not org:
            raise HTTPException(status_code=400, detail="GITHUB_TOKEN and GITHUB_ORG must be configured")

        templates = {
            "data-engineering": ["pipeline", "etl", "databricks", "spark"],
            "data-science": ["ml", "notebook", "databricks", "scikit-learn"],
            "databricks-apps": ["react", "fastapi", "databricks", "template"],
            "genie-room": ["genai", "ai-agent", "databricks", "llm"]
        }
        results = []

        async with GitHubProvider(token=token, org=org) as github:
            for template_name, topics in templates.items():
                # 1. Check if repo exists
                exists = await github.check_repo_exists(template_name)
                
                repo_path = f"{org}/{template_name}"
                headers = {
                    "Authorization": f"token {token}",
                    "Accept": "application/vnd.github.v3+json"
                }
                
                if not exists:
                    config = {
                        "description": f"Template for {template_name} projects",
                        "private": False, # Make them public so they are visible
                        "is_template": True
                    }
                    await github.create_repo(template_name, config)
                    results.append(f"Created {template_name}")
                else:
                    # Ensure it is marked as a template
                    async with httpx.AsyncClient() as client:
                        await client.patch(f"https://api.github.com/repos/{repo_path}", headers=headers, json={"is_template": True})
                    results.append(f"Ensured {template_name} is template")
                
                # Apply Topics (Tags)
                async with httpx.AsyncClient() as client:
                    # Topics API requires a specific accept header
                    topic_headers = headers.copy()
                    topic_headers["Accept"] = "application/vnd.github.mercury-preview+json"
                    await client.put(f"https://api.github.com/repos/{repo_path}/topics", headers=topic_headers, json={"names": topics})
                results.append(f"Applied tags to {template_name}: {topics}")
                
                # 2. Add boilerplate files
                files = {
                    "README.md": f"# {template_name} Template\nCreated for ATLAS testing on {datetime.now().isoformat()}",
                    "databricks.yml": "bundle:\n  name: my-bundle\n\nartifacts:\n  default:\n    type: wheel\n    build: python setup.py bdist_wheel",
                    ".gitignore": "__pycache__/\n*.pyc\n.databricks/\n"
                }
                
                for path, content in files.items():
                    url = f"https://api.github.com/repos/{org}/{template_name}/contents/{path}"
                    headers = {
                        "Authorization": f"token {token}",
                        "Accept": "application/vnd.github.v3+json"
                    }
                    
                    async with httpx.AsyncClient() as client:
                        get_resp = await client.get(url, headers=headers)
                        sha = None
                        if get_resp.status_code == 200:
                            sha = get_resp.json().get("sha")
                        
                        payload = {
                            "message": f"Seed {path}",
                            "content": base64.b64encode(content.encode()).decode(),
                        }
                        if sha:
                            payload["sha"] = sha
                        
                        await client.put(url, headers=headers, json=payload)

        return {"status": "success", "actions": results}
    except Exception as e:
        logger.error(f"Failed to setup GitHub templates: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/github/trigger-test")
async def trigger_github_test():
    """Trigger a mock GitHub Repo Creation request."""
    try:
        from app.db.session import get_session_local
        from app.db.request import RequestModel
        from app.models.request import RequestType, RequestStatus
        from datetime import datetime
        import uuid
        
        db = get_session_local()()
        try:
            timestamp = datetime.now().strftime("%Y%m%d-%H%M")
            repo_name = f"edh-test-project-{timestamp}"
            
            request_id = f"req-{uuid.uuid4()}"
            request = RequestModel(
                id=request_id,
                type=RequestType.GITHUB_REPO_CREATION.value,
                title=f"Test GitHub Repo: {repo_name}",
                status=RequestStatus.PENDING.value,
                current_state="pending",
                state_context={
                    "repo_name": repo_name,
                    "description": "Automated test repository created via trigger endpoint",
                    "visibility": "private",
                    "template": "data-engineering",
                    "requested_by": "System Test",
                    "requested_by_email": "admin@example.com"
                },
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            db.add(request)
            db.commit()
            return {
                "status": "success",
                "request_id": request_id,
                "repo_name": repo_name
            }
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Failed to trigger GitHub test: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
