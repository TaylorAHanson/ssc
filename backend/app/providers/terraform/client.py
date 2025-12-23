"""
Terraform provider client.
"""
from typing import Dict, Any, Optional
from app.providers.base import BaseProvider
from app.core.exceptions import RetryableError, PermanentError
from app.core.retry import retry_on_retryable
import subprocess
import asyncio
import json
import os
import shutil
import pathlib
import logging

logger = logging.getLogger(__name__)


class TerraformProvider(BaseProvider):
    """Terraform provider for infrastructure provisioning."""
    
    def __init__(self, workspace_dir: str, backend_config: Optional[Dict[str, Any]] = None, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.workspace_dir = workspace_dir
        self.backend_config = backend_config or {}
    
    @retry_on_retryable(max_attempts=3, min_wait=1.0, max_wait=8.0)
    async def apply(self, config: Dict[str, Any], variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Apply Terraform configuration.
        
        Args:
            config: Terraform configuration dictionary
            variables: Terraform variables
            
        Returns:
            Dictionary with apply results
        """
        try:
            # Write Terraform files
            self._write_tf_files(config)
            
            # Run terraform init
            init_result = await self._run_command(["terraform", "init"])
            if init_result["returncode"] != 0:
                self._classify_error(init_result["stderr"] or init_result["stdout"])
            
            # Run terraform apply
            cmd = ["terraform", "apply", "-auto-approve", "-json"]
            if variables:
                for key, value in variables.items():
                    cmd.extend(["-var", f"{key}={value}"])
            
            result = await self._run_command(cmd)
            
            # Check for errors
            if result["returncode"] != 0:
                # For -json, errors might be in stdout as diagnostic objects
                error_msg = self._extract_error_from_json(result["stdout"]) or result["stderr"]
                self._classify_error(error_msg)
            
            return self._parse_output(result)
            
        except (RetryableError, PermanentError):
            raise
        except ConnectionError as e:
            raise RetryableError(f"Connection error: {str(e)}")
        except TimeoutError as e:
            raise RetryableError(f"Timeout error: {str(e)}")
        except Exception as e:
            raise RetryableError(f"Unexpected error: {str(e)}")

    def _extract_error_from_json(self, stdout: str) -> Optional[str]:
        """Extract error message from Terraform JSON output."""
        if not stdout:
            return None
            
        errors = []
        for line in stdout.split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                if event.get("type") == "diagnostic":
                    diag = event.get("diagnostic", {})
                    if diag.get("severity") == "error":
                        summary = diag.get("summary", "")
                        detail = diag.get("detail", "")
                        msg = f"{summary}: {detail}" if detail else summary
                        if msg:
                            errors.append(msg)
            except json.JSONDecodeError:
                continue
        
        return "; ".join(errors) if errors else None
    
    async def destroy(self, config: Dict[str, Any]) -> bool:
        """Destroy infrastructure."""
        try:
            result = await self._run_command(["terraform", "destroy", "-auto-approve", "-json"])
            return result["returncode"] == 0
        except Exception as e:
            raise RetryableError(f"Destroy failed: {str(e)}")
    
    async def plan(self, config: Dict[str, Any], variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Plan infrastructure changes."""
        try:
            # Write Terraform files first
            self._write_tf_files(config)
            
            # Run terraform init
            await self._run_command(["terraform", "init"])
            
            # Run terraform plan
            cmd = ["terraform", "plan", "-json"]
            if variables:
                for key, value in variables.items():
                    cmd.extend(["-var", f"{key}={value}"])
            
            result = await self._run_command(cmd)
            return self._parse_output(result)
        except Exception as e:
            raise RetryableError(f"Plan failed: {str(e)}")
    
    async def get_state(self, resource_id: str) -> Dict[str, Any]:
        """Get Terraform state."""
        try:
            result = await self._run_command(["terraform", "show", "-json"])
            state = json.loads(result["stdout"])
            return state
        except Exception as e:
            raise RetryableError(f"Get state failed: {str(e)}")
    
    async def _run_command(self, cmd: list) -> Dict[str, Any]:
        """Run Terraform command with explicit environment loading."""
        try:
            # Load environment variables from terrarform_temp/.env if it exists
            # to ensure fresh AWS credentials are used
            env = os.environ.copy()
            
            project_root = pathlib.Path(__file__).parent.parent.parent.parent.parent
            env_file = project_root / "terrarform_temp" / ".env"
            
            if env_file.exists():
                try:
                    with open(env_file, "r") as f:
                        for line in f:
                            line = line.strip()
                            if not line or line.startswith("#"):
                                continue
                            if "=" in line:
                                key, value = line.split("=", 1)
                                # Remove quotes if present
                                value = value.strip("'\"")
                                env[key.strip()] = value
                    logger.info(f"Loaded environment variables from {env_file}")
                except Exception as e:
                    logger.warning(f"Failed to load .env file {env_file}: {e}")

            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=self.workspace_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env
            )
            stdout, stderr = await process.communicate()
            return {
                "stdout": stdout.decode() if stdout else "",
                "stderr": stderr.decode() if stderr else "",
                "returncode": process.returncode
            }
        except asyncio.TimeoutError:
            raise RetryableError("Command timeout")
        except Exception as e:
            raise RetryableError(f"Command execution failed: {str(e)}")
    
    def _write_tf_files(self, config: Dict[str, Any]):
        """
        Write Terraform configuration files.
        
        Copies template files from terrarform_temp/ to workspace directory
        and writes terraform.tfvars from config.
        
        Args:
            config: Dictionary containing:
                - terraform_template_dir: Path to template directory (optional, defaults to terrarform_temp)
                - terraform_tfvars: Dictionary of Terraform variables to write to terraform.tfvars
        """
        import pathlib
        
        # Get template directory (default to terrarform_temp relative to project root)
        template_dir = config.get("terraform_template_dir")
        if not template_dir:
            # Default to terrarform_temp in project root
            # __file__ is: backend/app/providers/terraform/client.py
            # Go up 4 levels to get to backend/, then up 1 more to project root
            project_root = pathlib.Path(__file__).parent.parent.parent.parent.parent
            template_dir = str(project_root / "terrarform_temp")
        
        if not os.path.exists(template_dir):
            raise PermanentError(f"Terraform template directory not found: {template_dir}")
        
        # Create workspace directory if it doesn't exist
        os.makedirs(self.workspace_dir, exist_ok=True)
        
        # Files to copy from template
        template_files = ["main.tf", "variables.tf"]
        
        # Copy template files
        for file_name in template_files:
            src_path = os.path.join(template_dir, file_name)
            dst_path = os.path.join(self.workspace_dir, file_name)
            
            if os.path.exists(src_path):
                shutil.copy2(src_path, dst_path)
                logger.info(f"Copied {file_name} to {self.workspace_dir}")
            else:
                logger.warning(f"Template file not found: {src_path}")
        
        # Write terraform.tfvars from config
        tfvars = config.get("terraform_tfvars", {})
        if tfvars:
            tfvars_path = os.path.join(self.workspace_dir, "terraform.tfvars")
            with open(tfvars_path, "w") as f:
                # Write as HCL format
                for key, value in tfvars.items():
                    if isinstance(value, dict):
                        # Handle nested dictionaries (like tags)
                        f.write(f"{key} = {{\n")
                        for nested_key, nested_value in value.items():
                            if isinstance(nested_value, str):
                                f.write(f'  {nested_key} = "{nested_value}"\n')
                            else:
                                f.write(f"  {nested_key} = {nested_value}\n")
                        f.write("}\n")
                    elif isinstance(value, str):
                        f.write(f'{key} = "{value}"\n')
                    elif isinstance(value, (int, float, bool)):
                        f.write(f"{key} = {value}\n")
                    elif isinstance(value, list):
                        # Handle lists
                        f.write(f"{key} = [\n")
                        for item in value:
                            if isinstance(item, str):
                                f.write(f'  "{item}",\n')
                            else:
                                f.write(f"  {item},\n")
                        f.write("]\n")
                    else:
                        f.write(f'{key} = "{value}"\n')
            
            logger.info(f"Wrote terraform.tfvars to {tfvars_path}")
        
        logger.info(f"Terraform files written to {self.workspace_dir}")
    
    def _parse_output(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse Terraform JSON output to extract workspace information.
        
        Terraform outputs JSON lines when using -json flag. Each line is a JSON object
        with a "type" field. We're looking for:
        - "outputs" type: Contains the output values
        - "apply_complete" type: Indicates successful completion
        
        Args:
            result: Dictionary with stdout, stderr, returncode
            
        Returns:
            Dictionary with:
                - success: bool
                - workspace_url: str (from databricks_host output)
                - workspace_id: str (extracted from URL or output)
                - workspace_token: str (from databricks_token output, if available)
        """
        if result["returncode"] != 0:
            return {
                "success": False,
                "error": result.get("stderr", "Unknown error")
            }
        
        outputs = {}
        apply_complete = False
        
        # Parse JSON lines from stdout
        for line in result["stdout"].split("\n"):
            line = line.strip()
            if not line:
                continue
            
            try:
                event = json.loads(line)
                event_type = event.get("type")
                
                if event_type == "outputs":
                    # Extract output values
                    output_changes = event.get("outputs", {})
                    for output_name, output_data in output_changes.items():
                        if "value" in output_data:
                            outputs[output_name] = output_data["value"]
                
                elif event_type == "apply_complete":
                    apply_complete = True
                    
            except json.JSONDecodeError:
                # Skip non-JSON lines (like warnings or other output)
                continue
            except Exception as e:
                logger.warning(f"Error parsing Terraform output line: {e}")
                continue
        
        # Extract workspace information from outputs
        workspace_url = outputs.get("databricks_host", "")
        workspace_token = outputs.get("databricks_token", "")
        
        # Extract workspace_id from URL if available
        # Format: https://<workspace-id>.cloud.databricks.com
        workspace_id = None
        if workspace_url:
            try:
                # Parse URL to extract workspace ID
                # URL format: https://<workspace-id>.cloud.databricks.com
                if "//" in workspace_url:
                    hostname = workspace_url.split("//")[1].split("/")[0]
                    if "." in hostname:
                        workspace_id = hostname.split(".")[0]
            except Exception as e:
                logger.warning(f"Could not extract workspace_id from URL: {e}")
        
        return {
            "success": apply_complete and result["returncode"] == 0,
            "workspace_url": workspace_url,
            "workspace_id": workspace_id,
            "workspace_token": workspace_token,
            "outputs": outputs
        }
    
    def _classify_error(self, error_msg: str):
        """Classify Terraform error as retryable or permanent."""
        error_lower = error_msg.lower()
        
        if "expiredtoken" in error_lower or "security token included in the request is expired" in error_lower:
            raise PermanentError(f"AWS Credentials Expired: Please refresh your AWS session token and restart the backend. (Error: {error_msg})")
        elif "state lock" in error_lower:
            raise RetryableError(f"Terraform state locked: {error_msg}")
        elif "authentication" in error_lower or "unauthorized" in error_lower:
            raise PermanentError(f"Authentication failed: {error_msg}")
        elif "validation" in error_lower or "invalid" in error_lower:
            raise PermanentError(f"Validation error: {error_msg}")
        else:
            raise RetryableError(f"Terraform error: {error_msg}")
    
    async def health_check(self) -> bool:
        """Check if Terraform is available."""
        try:
            result = await self._run_command(["terraform", "version"])
            return result["returncode"] == 0
        except:
            return False

