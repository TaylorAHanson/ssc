import os
import json
import subprocess
import httpx
import logging
from typing import Dict, Any

from app.providers.base import BaseProvider
from app.core.exceptions import RetryableError, PermanentError

logger = logging.getLogger(__name__)

class OpaProvider(BaseProvider):
    """
    Provider for evaluating Open Policy Agent (OPA) Rego policies.
    It can be configured to use a remote OPA server via REST or a local OPA binary.
    """
    def __init__(self, config: dict = None):
        super().__init__(config)
        # Check if we should use a remote server (e.g. http://opa:8181)
        self.opa_url = self.config.get("opa_url")
        self.use_local_binary = self.config.get("use_local_binary", True)
        self.policies_dir = self.config.get("policies_dir", "policies")

    def health_check(self) -> bool:
        if self.opa_url:
            try:
                response = httpx.get(f"{self.opa_url}/health")
                return response.status_code == 200
            except Exception:
                return False
        elif self.use_local_binary:
            try:
                subprocess.run(["opa", "version"], capture_output=True, check=True)
                return True
            except FileNotFoundError:
                logger.error("OPA binary not found in PATH")
                return False
        return False

    async def evaluate(self, policy_path: str, query: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Evaluate a Rego policy.
        
        :param policy_path: Path to the .rego file or package name.
        :param query: The data path to query (e.g., "data.databricks.governance.asset_allowlist").
        :param input_data: The input dictionary to provide to OPA.
        :return: The evaluation result.
        """
        if self.opa_url:
            return await self._evaluate_remote(query, input_data)
        elif self.use_local_binary:
            return await self._evaluate_local(policy_path, query, input_data)
        else:
            raise PermanentError("OPA provider not configured for remote or local execution")

    async def _evaluate_remote(self, query: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
        endpoint = f"{self.opa_url}/v1/data/{query.replace('data.', '').replace('.', '/')}"
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(endpoint, json={"input": input_data})
                response.raise_for_status()
                return response.json().get("result", {})
        except httpx.RequestError as e:
            raise RetryableError(f"Failed to communicate with OPA server: {str(e)}")
        except httpx.HTTPStatusError as e:
            raise PermanentError(f"OPA server returned error: {e.response.text}")

    async def _evaluate_local(self, policy_file: str, query: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
        # Create a temporary input file
        import tempfile
        
        policy_full_path = os.path.join(os.getcwd(), self.policies_dir, policy_file)
        if not os.path.exists(policy_full_path):
            raise PermanentError(f"Policy file not found: {policy_full_path}")

        with tempfile.NamedTemporaryFile('w', delete=False, suffix='.json') as temp_in:
            json.dump(input_data, temp_in)
            temp_in_path = temp_in.name

        try:
            # Run opa eval
            cmd = [
                "opa", "eval",
                "-d", policy_full_path,
                "-i", temp_in_path,
                "-f", "values",
                query
            ]
            
            process = subprocess.run(cmd, capture_output=True, text=True)
            
            if process.returncode != 0:
                raise PermanentError(f"OPA evaluation failed: {process.stderr}")
                
            output = json.loads(process.stdout)
            if not output:
                return {}
            # Output is typically a list of results, we want the first one
            return output[0] if isinstance(output, list) else output

        finally:
            if os.path.exists(temp_in_path):
                os.remove(temp_in_path)
