import json
import logging
import os
import shutil
import subprocess
from typing import Any, Dict, Optional

import httpx

from app.core.exceptions import PermanentError, RetryableError
from app.providers.base import BaseProvider

logger = logging.getLogger(__name__)

OPA_SETUP_HINT = (
    "Open Policy Agent (opa) is not available. Install it (e.g. `brew install opa`) so `opa` is on PATH, "
    "or set OPA_BINARY_PATH in .env to the executable, or set OPA_URL to a running OPA server "
    "that already has your Rego bundles loaded. See https://www.openpolicyagent.org/docs/latest/"
)


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
        self.opa_binary = self.config.get("opa_binary")

    def _resolve_opa_executable(self) -> Optional[str]:
        """Return path to the OPA CLI, or None if not found."""
        logger.info("[opa-install] Resolving OPA executable...")
        configured = (self.opa_binary or "").strip() if self.opa_binary else ""
        if configured:
            logger.info(f"[opa-install] Checking configured OPA path: {configured}")
            expanded = os.path.expanduser(configured)
            if os.path.isfile(expanded):
                logger.info(f"[opa-install] Found OPA at configured path: {expanded}")
                return expanded
            logger.warning(f"[opa-install] Configured OPA path {expanded} is not a file.")
            return None
            
        # 1. Try to find a bundled binary first (e.g. backend/bin/opa_linux_amd64)
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        bundled_linux = os.path.join(base_dir, "bin", "opa_linux_amd64")
        logger.info(f"[opa-install] Checking for bundled OPA binary at: {bundled_linux}")
        
        if os.path.isfile(bundled_linux):
            logger.info(f"[opa-install] Bundled OPA binary found at {bundled_linux}. Checking executable permissions...")
            # Check if it's already executable
            if os.access(bundled_linux, os.X_OK):
                logger.info(f"[opa-install] Bundled OPA binary at {bundled_linux} is executable.")
                return bundled_linux
                
            # If not executable, it might be in a read-only filesystem (Databricks Apps)
            # Copy it to /tmp and make it executable there
            logger.info(f"[opa-install] Bundled OPA binary is NOT executable. Copying to /tmp...")
            dest_path = "/tmp/opa_linux_amd64"
            try:
                import stat
                if not os.path.exists(dest_path):
                    logger.info(f"[opa-install] Copying {bundled_linux} to {dest_path}")
                    shutil.copy2(bundled_linux, dest_path)
                else:
                    logger.info(f"[opa-install] File already exists at {dest_path}, verifying size...")
                    if os.path.getsize(bundled_linux) != os.path.getsize(dest_path):
                        logger.info(f"[opa-install] Size mismatch, overwriting {dest_path}")
                        shutil.copy2(bundled_linux, dest_path)
                        
                logger.info(f"[opa-install] Setting executable permissions on {dest_path}")
                st = os.stat(dest_path)
                os.chmod(dest_path, st.st_mode | stat.S_IEXEC)
                if os.access(dest_path, os.X_OK):
                    logger.info(f"[opa-install] Successfully made {dest_path} executable. Using it.")
                    return dest_path
                else:
                    logger.error(f"[opa-install] Failed to verify executable permissions on {dest_path} after chmod.")
            except Exception as e:
                logger.warning(f"[opa-install] Failed to copy and make {bundled_linux} executable in /tmp: {e}")
        else:
            logger.info(f"[opa-install] Bundled OPA binary NOT found at {bundled_linux}")
            
            # 1.5. Fallback: If not found, try downloading it to /tmp
            dest_path = "/tmp/opa_linux_amd64"
            if os.path.isfile(dest_path) and os.access(dest_path, os.X_OK):
                logger.info(f"[opa-install] Found previously downloaded OPA binary at {dest_path}")
                return dest_path
                
            logger.info(f"[opa-install] Attempting to download OPA to {dest_path}...")
            try:
                import stat
                import urllib.request
                opa_url = "https://openpolicyagent.org/downloads/v0.61.0/opa_linux_amd64_static"
                urllib.request.urlretrieve(opa_url, dest_path)
                
                logger.info(f"[opa-install] Download complete. Setting executable permissions...")
                st = os.stat(dest_path)
                os.chmod(dest_path, st.st_mode | stat.S_IEXEC)
                
                if os.access(dest_path, os.X_OK):
                    logger.info(f"[opa-install] Successfully downloaded and prepared OPA at {dest_path}")
                    return dest_path
                else:
                    logger.error(f"[opa-install] Failed to make downloaded OPA executable.")
            except Exception as e:
                logger.warning(f"[opa-install] Failed to dynamically download OPA: {e}")
            
        # 2. Try looking in the system PATH
        logger.info("[opa-install] Looking for 'opa' in system PATH...")
        found = shutil.which("opa")
        if found:
            logger.info(f"[opa-install] Found 'opa' in system PATH at: {found}")
            return found
            
        logger.error("[opa-install] Could not resolve OPA executable by any method.")
        return None

    def _require_local_opa(self) -> str:
        exe = self._resolve_opa_executable()
        if not exe:
            raise PermanentError(OPA_SETUP_HINT)
        return exe

    def health_check(self) -> bool:
        if self.opa_url:
            try:
                response = httpx.get(f"{self.opa_url}/health")
                return response.status_code == 200
            except Exception:
                return False
        elif self.use_local_binary:
            return self._resolve_opa_executable() is not None
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
        import tempfile

        opa_exe = self._require_local_opa()

        policies_dir_path = os.path.join(os.getcwd(), self.policies_dir)
        if not os.path.exists(policies_dir_path):
            raise PermanentError(f"Policies directory not found: {policies_dir_path}")

        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json") as temp_in:
            json.dump(input_data, temp_in)
            temp_in_path = temp_in.name

        try:
            cmd = [
                opa_exe,
                "eval",
                "-d",
                policies_dir_path,
                "-i",
                temp_in_path,
                "-f",
                "values",
                query,
            ]

            try:
                process = subprocess.run(cmd, capture_output=True, text=True)
            except FileNotFoundError as e:
                raise PermanentError(OPA_SETUP_HINT) from e

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
