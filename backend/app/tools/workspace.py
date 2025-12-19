"""
Workspace tools.
"""
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from app.tools.base import BaseTool
from app.providers.terraform import TerraformProvider
from app.providers.databricks import DatabricksProvider
from app.providers.idp import IDPProvider
from app.providers.notifications import NotificationProvider
from app.state_machines.facts import has_fact, add_fact, get_fact_data
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)


class CreateWorkspaceTool(BaseTool):
    """Create a Databricks workspace."""
    
    def __init__(self):
        """Initialize providers from settings."""
        # Terraform provider - workspace_dir will be set per request in execute()
        # Store base directory from settings for creating per-request workspaces
        self.terraform_base_dir = settings.TERRAFORM_WORKSPACE_BASE_DIR
        # Initialize with base directory - will create per-request workspace in execute()
        self.terraform = TerraformProvider(
            workspace_dir=self.terraform_base_dir,
            config={}
        )
        
        # Databricks provider - only initialize if credentials are available
        # This is optional since create_workspace is not yet implemented
        # Terraform handles workspace provisioning, so this is for future workspace operations
        self.databricks = None
        if settings.DATABRICKS_HOST and settings.DATABRICKS_TOKEN:
            try:
                self.databricks = DatabricksProvider(
                    host=settings.DATABRICKS_HOST,
                    token=settings.DATABRICKS_TOKEN
                )
            except Exception as e:
                logger.warning(f"Could not initialize DatabricksProvider: {e}. Workspace operations will be limited.")
                self.databricks = None
        
        # IDP provider (optional - only if configured)
        self.idp = IDPProvider(
            base_url=settings.IDP_BASE_URL or "",
            api_key=settings.IDP_API_KEY or ""
        )
        
        # Notification provider
        notification_config = {
            "email": {
                "smtp_host": settings.NOTIFICATION_EMAIL_SMTP_HOST,
                "smtp_port": settings.NOTIFICATION_EMAIL_SMTP_PORT,
                "smtp_user": settings.NOTIFICATION_EMAIL_SMTP_USER,
                "smtp_password": settings.NOTIFICATION_EMAIL_SMTP_PASSWORD,
            },
            "slack": {
                "webhook_url": settings.NOTIFICATION_SLACK_WEBHOOK_URL,
            },
            "teams": {
                "webhook_url": settings.NOTIFICATION_TEAMS_WEBHOOK_URL,
            }
        }
        self.notifications = NotificationProvider(config=notification_config)
    
    async def execute(
        self,
        request_id: str,
        name: str,
        environment: str,
        config: Dict[str, Any],
        requested_by: str,
        db: Session
    ) -> Dict[str, Any]:
        """
        Create workspace using providers with idempotency guard.
        
        This implements the "Guard Pattern" - checks facts before executing.
        If workspace already exists (fact: workspace_created), skip creation.
        This makes the operation idempotent and self-healing.
        """
        # IDEMPOTENCY GUARD: Check if workspace already exists
        if has_fact(db, request_id, "workspace_created"):
            logger.info(f"Workspace already exists for request {request_id}, skipping creation")
            existing_data = get_fact_data(db, request_id, "workspace_created", {})
            return {
                "workspace_id": existing_data.get("workspace_id"),
                "workspace_url": existing_data.get("workspace_url"),
                "status": "already_exists",
                "skipped": True
            }
        
        # Record that provisioning started
        add_fact(db, request_id, "provisioning_started", {
            "workspace_name": name,
            "environment": environment
        }, actor="system")
        self.report_progress(db, request_id, "Initializing infrastructure provisioning...", 10)
        
        try:
            # Step 1: Provision infrastructure via Terraform
            # Create unique workspace directory per request to avoid conflicts
            import os
            workspace_dir = os.path.join(self.terraform_base_dir, f"workspace-{request_id}")
            os.makedirs(workspace_dir, exist_ok=True)
            
            # Update Terraform provider's workspace directory for this request
            # (or create new instance - using same instance but updating dir)
            original_workspace_dir = self.terraform.workspace_dir
            self.terraform.workspace_dir = workspace_dir
            
            try:
                self.report_progress(db, request_id, "Preparing Terraform configuration...", 20)
                tf_config = self._build_terraform_config(name, environment, config)
                
                self.report_progress(db, request_id, "Applying infrastructure changes (this may take 5-10 minutes)...", 40)
                tf_result = await self.terraform.apply(tf_config, variables=config)
            finally:
                # Restore original workspace_dir (for potential reuse, though typically new instance per request)
                self.terraform.workspace_dir = original_workspace_dir
            
            # Extract workspace information from Terraform output
            workspace_url = tf_result.get("workspace_url", "")
            workspace_id = tf_result.get("workspace_id")
            
            if not workspace_url:
                raise ValueError("Terraform did not return workspace_url")
            
            self.report_progress(db, request_id, "Infrastructure provisioned. Configuring workspace...", 70)
            
            # Extract workspace_id from URL if not provided
            if not workspace_id and workspace_url:
                # URL format: https://<workspace-id>.cloud.databricks.com
                try:
                    hostname = workspace_url.split("//")[1].split("/")[0]
                    workspace_id = hostname.split(".")[0] if "." in hostname else None
                except Exception as e:
                    logger.warning(f"Could not extract workspace_id from URL: {e}")
            
            # Step 2: Configure Databricks workspace (if needed)
            # Note: For serverless workspaces, Terraform already creates the workspace
            # This step might be for additional configuration
            db_result = {}
            if self.databricks:
                try:
                    self.report_progress(db, request_id, "Setting up Databricks objects...", 80)
                    db_result = await self.databricks.create_workspace(
                        name=name,
                        config=config.get("databricks_config", {})
                    )
                except NotImplementedError:
                    # Databricks provider not fully implemented yet - that's okay
                    # Terraform already created the workspace
                    logger.info("Databricks provider create_workspace not implemented, using Terraform result")
                    db_result = {"url": workspace_url}
            else:
                # Databricks provider not available - that's okay, Terraform already created the workspace
                logger.info("Databricks provider not available, using Terraform result")
                db_result = {"url": workspace_url}
            
            # Step 3: Grant access to requester (if IDP provider is configured)
            try:
                self.report_progress(db, request_id, "Granting user permissions...", 90)
                await self.idp.grant_permission(
                    principal_id=requested_by,
                    resource=f"workspace:{workspace_id or name}",
                    permissions=config.get("permissions", ["user"])
                )
            except Exception as e:
                # IDP provider might not be fully implemented - log and continue
                logger.warning(f"Could not grant IDP permissions: {e}")
            
            self.report_progress(db, request_id, "Finalizing setup...", 95)
            
            # Step 4: Record fact that workspace was created (source of truth)
            add_fact(db, request_id, "workspace_created", {
                "workspace_id": workspace_id or name,
                "workspace_url": workspace_url,
                "workspace_name": name,
                "environment": environment
            }, actor="terraform")
            
            # Step 5: Record provisioning completed
            add_fact(db, request_id, "provisioning_completed", {
                "workspace_id": workspace_id
            }, actor="system")
            
            self.report_progress(db, request_id, "Workspace provisioned successfully!", 100)
            
            # Step 6: Notify user
            try:
                await self.notifications.send_email(
                    to=requested_by,
                    subject=f"Workspace {name} has been provisioned",
                    body=f"Your workspace is ready at {workspace_url}"
                )
            except Exception as e:
                # Notification provider might not be fully implemented - log and continue
                logger.warning(f"Could not send notification: {e}")
            
            return {
                "workspace_id": workspace_id or name,
                "workspace_url": workspace_url,
                "status": "completed"
            }
        except Exception as e:
            # Record failure fact
            add_fact(db, request_id, "provisioning_failed", {
                "error": str(e),
                "workspace_name": name
            }, actor="system")
            raise
    
    def _build_terraform_config(self, name: str, environment: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build Terraform configuration from request data.
        
        Args:
            name: Workspace name
            environment: Environment (dev, test, stage, prod)
            config: Request configuration dictionary containing:
                - databricks_account_id: Databricks account ID (required)
                - client_id: Databricks service principal client ID (required)
                - client_secret: Databricks service principal client secret (required)
                - region: AWS region (optional, defaults to eu-west-1)
                - cidr_block: VPC CIDR block (optional, defaults to 10.4.0.0/16)
                - tags: Additional tags (optional)
        
        Returns:
            Dictionary with terraform_tfvars structure for TerraformProvider
        """
        # Required values from config
        databricks_account_id = config.get("databricks_account_id")
        client_id = config.get("client_id")
        client_secret = config.get("client_secret")
        
        if not databricks_account_id:
            raise ValueError("databricks_account_id is required in config")
        if not client_id:
            raise ValueError("client_id is required in config")
        if not client_secret:
            raise ValueError("client_secret is required in config")
        
        # Optional values with defaults
        region = config.get("region", "eu-west-1")
        cidr_block = config.get("cidr_block", "10.4.0.0/16")
        
        # Build tags
        tags = {
            "Name": name,
            "Environment": environment,
            "Project": "edas-hub",
            "ManagedBy": "terraform",
            "WorkspaceName": name,
        }
        
        # Merge with any additional tags from config
        if "tags" in config and isinstance(config["tags"], dict):
            tags.update(config["tags"])
        
        # Build terraform_tfvars structure
        terraform_tfvars = {
            "databricks_account_id": databricks_account_id,
            "client_id": client_id,
            "client_secret": client_secret,
            "region": region,
            "cidr_block": cidr_block,
            "tags": tags
        }
        
        logger.info(f"Built Terraform config for workspace '{name}' in environment '{environment}'")
        
        return {
            "terraform_tfvars": terraform_tfvars
        }

