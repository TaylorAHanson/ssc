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
            await self._run_command(["terraform", "init"])
            
            # Run terraform apply
            cmd = ["terraform", "apply", "-auto-approve", "-json"]
            if variables:
                for key, value in variables.items():
                    cmd.extend(["-var", f"{key}={value}"])
            
            result = await self._run_command(cmd)
            
            # Check for errors
            if result["returncode"] != 0:
                error_msg = result["stderr"]
                self._classify_error(error_msg)
            
            return self._parse_output(result)
            
        except ConnectionError as e:
            raise RetryableError(f"Connection error: {str(e)}")
        except TimeoutError as e:
            raise RetryableError(f"Timeout error: {str(e)}")
        except PermanentError:
            raise
        except Exception as e:
            raise RetryableError(f"Unexpected error: {str(e)}")
    
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
        """Run Terraform command."""
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=self.workspace_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
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
        """Write Terraform configuration files."""
        # TODO: Implement Terraform file writing
        pass
    
    def _parse_output(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Parse Terraform output."""
        # TODO: Implement output parsing
        return {"success": result["returncode"] == 0}
    
    def _classify_error(self, error_msg: str):
        """Classify Terraform error as retryable or permanent."""
        error_lower = error_msg.lower()
        
        if "state lock" in error_lower:
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

