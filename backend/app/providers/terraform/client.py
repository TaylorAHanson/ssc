"""
Terraform provider client (GitOps Pattern).
"""
from typing import Dict, Any, Optional, List
from app.providers.base import BaseProvider
from app.core.exceptions import RetryableError, PermanentError
from app.core.retry import retry_on_retryable
import os
import shutil
import logging
import yaml
import git
import time
import requests
from pathlib import Path

logger = logging.getLogger(__name__)


def generate_github_app_token(app_id: str, private_key: str, installation_id: str = None) -> str:
    """
    Generate a GitHub App installation access token.
    
    Args:
        app_id: GitHub App ID
        private_key: PEM-encoded private key
        installation_id: Optional installation ID (will be fetched if not provided)
    
    Returns:
        Installation access token for git operations
    """
    try:
        import jwt
    except ImportError:
        logger.error("PyJWT not installed. Run: pip install PyJWT")
        raise RetryableError("PyJWT library not available")
    
    # Generate JWT
    now = int(time.time())
    payload = {
        "iat": now - 60,  # Issued 60 seconds ago (clock skew)
        "exp": now + (10 * 60),  # Expires in 10 minutes
        "iss": app_id
    }
    
    try:
        encoded_jwt = jwt.encode(payload, private_key, algorithm="RS256")
    except Exception as e:
        logger.error(f"Failed to generate JWT: {e}")
        raise RetryableError(f"JWT generation failed: {e}")
    
    headers = {
        "Authorization": f"Bearer {encoded_jwt}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    
    # Get installation ID if not provided
    if not installation_id:
        resp = requests.get("https://api.github.com/app/installations", headers=headers)
        if resp.status_code != 200:
            logger.error(f"Failed to get installations: {resp.text}")
            raise RetryableError(f"Failed to get GitHub App installations: {resp.status_code}")
        
        installations = resp.json()
        if not installations:
            raise PermanentError("GitHub App has no installations")
        
        # Use first installation (or find specific one by org/repo)
        installation_id = installations[0]["id"]
        logger.info(f"Using GitHub App installation ID: {installation_id}")
    
    # Get installation access token
    resp = requests.post(
        f"https://api.github.com/app/installations/{installation_id}/access_tokens",
        headers=headers
    )
    
    if resp.status_code != 201:
        logger.error(f"Failed to get installation token: {resp.text}")
        raise RetryableError(f"Failed to get installation token: {resp.status_code}")
    
    token_data = resp.json()
    return token_data["token"]


class TerraformProvider(BaseProvider):
    """
    Terraform provider implementing GitOps pattern.
    
    Acts as a bridge to an infrastructure-as-code repository.
    Modifies YAML configuration files and relies on external CD to apply changes.
    """
    
    def __init__(self, repo_url: str, branch: str = "main", config: Optional[Dict[str, Any]] = None):
        """
        Initialize GitOps Provider.
        
        Args:
            repo_url: URL of the infrastructure git repository.
            branch: Main branch to operate on (default: main).
            config: Additional configuration including:
                - local_repo_path: Where to clone the repo
                - git_username: Git commit author name
                - git_email: Git commit author email
                - ssh_key_path: Path to SSH key (for SSH auth)
                - git_token: GitHub PAT (for simple HTTPS auth)
                - github_app_id: GitHub App ID (for GitHub App auth)
                - github_app_private_key: PEM-encoded private key
                - github_app_installation_id: Optional installation ID
        """
        super().__init__(config)
        self.repo_url = repo_url
        self.main_branch = branch
        self.local_repo_path = self.config.get("local_repo_path", "/tmp/infra-repo")
        self.username = self.config.get("git_username", "ATLAS Bot")
        self.email = self.config.get("git_email", "atlas-bot@databricks.com")
        self.ssh_key_path = self.config.get("ssh_key_path")
        self.git_token = self.config.get("git_token")  # GitHub PAT for HTTPS auth
        
        # GitHub App authentication (preferred over PAT)
        self.github_app_id = self.config.get("github_app_id")
        self.github_app_private_key = self.config.get("github_app_private_key")
        self.github_app_installation_id = self.config.get("github_app_installation_id")
        self._cached_token = None
        self._token_expires_at = 0
        
    def _get_github_app_token(self) -> Optional[str]:
        """Get or refresh GitHub App installation token."""
        if not self.github_app_id or not self.github_app_private_key:
            return None
            
        # Check if we have a valid cached token (with 5 min buffer)
        if self._cached_token and time.time() < (self._token_expires_at - 300):
            return self._cached_token
        
        # Generate new token
        self._cached_token = generate_github_app_token(
            self.github_app_id,
            self.github_app_private_key,
            self.github_app_installation_id
        )
        # Installation tokens are valid for 1 hour
        self._token_expires_at = time.time() + 3600
        logger.info("Generated new GitHub App installation token")
        return self._cached_token
        
    def _get_authenticated_url(self) -> str:
        """Get repo URL with authentication credentials if using HTTPS."""
        if not self.repo_url:
            return ""
        
        if not self.repo_url.startswith("https://"):
            return self.repo_url
            
        # Try GitHub App authentication first
        app_token = self._get_github_app_token()
        if app_token:
            return self.repo_url.replace("https://", f"https://x-access-token:{app_token}@")
        
        # Fall back to PAT if provided
        if self.git_token:
            return self.repo_url.replace("https://", f"https://x-access-token:{self.git_token}@")
        
        return self.repo_url
        
    @retry_on_retryable(max_attempts=3)
    async def plan(self, request_id: str, target_file: str, content: Dict[str, Any], commit_message: str) -> Dict[str, Any]:
        """
        Prepare infrastructure changes in a dedicated branch.
        
        1. Checkout/Create branch 'request/{request_id}'
        2. Update target yaml file
        3. Commit and push
        
        Args:
            request_id: Unique request identifier.
            target_file: Path to YAML file relative to repo root.
            content: Dictionary content to merge/write.
            commit_message: Message for the git commit.
        """
        try:
            repo = self._prepare_repo()
            
            # Switch to request branch
            branch_name = f"request/{request_id}"
            await self._checkout_branch(repo, branch_name)
            
            # File Operations
            full_path = Path(self.local_repo_path) / target_file
            full_path.parent.mkdir(parents=True, exist_ok=True)
            
            current_content = {}
            if full_path.exists():
                with open(full_path, "r") as f:
                    current_content = yaml.safe_load(f) or {}
            
            # Merge content
            current_content.update(content)
            
            with open(full_path, "w") as f:
                yaml.dump(current_content, f, default_flow_style=False)
            
            result = self._commit_and_push(repo, target_file, commit_message, branch_name)
            
            logger.info(f"Plan branch {branch_name} pushed. CI should trigger plan.")
            return result
            
        except git.GitCommandError as e:
            raise RetryableError(f"Git operation failed: {str(e)}")
        except Exception as e:
            raise RetryableError(f"Unexpected error: {str(e)}")

    @retry_on_retryable(max_attempts=3)
    async def apply(self, request_id: str) -> Dict[str, Any]:
        """
        Apply infrastructure changes by merging to main.
        
        1. Checkout main
        2. Merge 'request/{request_id}'
        3. Push main
        
        Args:
            request_id: Unique request identifier.
        """
        try:
            repo = self._prepare_repo()
            branch_name = f"request/{request_id}"
            
            # Ensure we have the latest main
            repo.git.checkout(self.main_branch)
            repo.remotes.origin.pull()
            
            # Merge request branch
            # We use --no-ff to create a merge commit for history
            try:
                repo.git.merge(branch_name, "--no-ff", "-m", f"Merge request {request_id}")
            except git.GitCommandError as e:
                # Handle merge conflicts if needed, for now treat as failure
                raise PermanentError(f"Merge conflict or failure: {str(e)}")
            
            # Push main
            self._push_with_retry(repo, self.main_branch)
            
            # Optional: Delete branch locally and remotely
            try:
                repo.delete_head(branch_name, force=True)
                repo.remotes.origin.push(refspec=f":{branch_name}") 
            except Exception as e:
                logger.warning(f"Failed to delete branch {branch_name}: {e}")

            logger.info(f"Merged {branch_name} to {self.main_branch}. CI should trigger apply.")
            return {"status": "merged", "branch": self.main_branch}
            
        except git.GitCommandError as e:
            raise RetryableError(f"Git operation failed: {str(e)}")
        except Exception as e:
            raise RetryableError(f"Unexpected error: {str(e)}")

    @retry_on_retryable(max_attempts=3)
    async def upsert_resource(self, target_file: str, content: Dict[str, Any], commit_message: str) -> Dict[str, Any]:
        """Legacy direct upsert - preserved for compatibility but should use plan()"""
        # This implementation simply pushes to main directly
        # You might want to deprecate this or make it use a temp branch and auto-merge
        return await self.plan("direct-update", target_file, content, commit_message)

    async def _checkout_branch(self, repo: git.Repo, branch_name: str):
        """Checkout a branch, creating it from main if it doesn't exist."""
        # Ensure we start from fresh main
        repo.git.checkout(self.main_branch)
        repo.remotes.origin.pull()
        
        try:
            repo.git.checkout(branch_name)
        except git.GitCommandError:
            # Create if doesn't exist
            repo.git.checkout("-b", branch_name)

    async def get_resource(self, target_file: str) -> Optional[Dict[str, Any]]:
        """
        Read the current configuration from the local git repo (main branch).
        """
        try:
            repo = self._prepare_repo()
            repo.git.checkout(self.main_branch)
            repo.remotes.origin.pull()
            
            full_path = Path(self.local_repo_path) / target_file
            if full_path.exists():
                with open(full_path, "r") as f:
                    return yaml.safe_load(f)
            return None
        except Exception as e:
            logger.error(f"Failed to read resource {target_file}: {e}")
            return None

    def _commit_and_push(self, repo: git.Repo, target_file: str, message: str, branch_name: str) -> Dict[str, Any]:
        """Helper to commit and push changes."""
        if repo.is_dirty(untracked_files=True):
            full_path = str(Path(self.local_repo_path) / target_file)
            repo.index.add([full_path])
            commit = repo.index.commit(message, author=git.Actor(self.username, self.email))
            
            self._push_with_retry(repo, branch_name)
            
            return {
                "success": True,
                "commit_sha": commit.hexsha,
                "status": "pushed"
            }
        else:
            return {
                "success": True,
                "commit_sha": repo.head.commit.hexsha,
                "status": "no_change"
            }

    def _prepare_repo(self) -> git.Repo:
        """Clone or load and pull the repository."""
        repo_path = Path(self.local_repo_path)
        auth_url = self._get_authenticated_url()
        
        if self.ssh_key_path:
            os.environ["GIT_SSH_COMMAND"] = f"ssh -i {self.ssh_key_path} -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no"
            
        try:
            if repo_path.exists() and (repo_path / ".git").exists():
                repo = git.Repo(repo_path)
                # Check if the base repo URL matches (ignoring auth tokens in URL)
                current_url = repo.remotes.origin.url
                base_repo_url = self.repo_url.split("@")[-1] if "@" in self.repo_url else self.repo_url
                current_base_url = current_url.split("@")[-1] if "@" in current_url else current_url
                
                if base_repo_url not in current_base_url and current_base_url not in base_repo_url:
                    shutil.rmtree(repo_path)
                    repo = git.Repo.clone_from(auth_url, repo_path, branch=self.main_branch)
                else:
                    # Update remote URL in case token changed
                    repo.remotes.origin.set_url(auth_url)
            else:
                if repo_path.exists():
                    shutil.rmtree(repo_path)
                repo = git.Repo.clone_from(auth_url, repo_path, branch=self.main_branch)
                
            with repo.config_writer() as cw:
                cw.set_value("user", "name", self.username)
                cw.set_value("user", "email", self.email)
            return repo
        except Exception as e:
            raise RetryableError(f"Repo preparation failed: {str(e)}")

    def _push_with_retry(self, repo: git.Repo, branch_name: str):
        """Push with rebase on conflict."""
        for attempt in range(3):
            try:
                repo.remotes.origin.push(refspec=f"{branch_name}:{branch_name}")
                return
            except git.GitCommandError:
                if attempt < 2:
                    repo.git.pull("--rebase", "origin", branch_name)
                else:
                    raise RetryableError("Push failed after retries")

    async def health_check(self) -> bool:
        """Check git connectivity."""
        try:
            self._prepare_repo()
            return True
        except:
            return False
