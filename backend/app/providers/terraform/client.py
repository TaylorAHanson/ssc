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
from pathlib import Path

logger = logging.getLogger(__name__)


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
            branch: Branch to operate on (default: main).
            config: Additional configuration (local_repo_path, etc.).
        """
        super().__init__(config)
        self.repo_url = repo_url
        self.branch = branch
        self.local_repo_path = self.config.get("local_repo_path", "/tmp/infra-repo")
        self.username = self.config.get("git_username", "Ops Bot")
        self.email = self.config.get("git_email", "ops-bot@example.com")
        self.ssh_key_path = self.config.get("ssh_key_path")
        
    @retry_on_retryable(max_attempts=3)
    async def upsert_resource(self, target_file: str, content: Dict[str, Any], commit_message: str) -> Dict[str, Any]:
        """
        Create or update a resource configuration file.
        
        Args:
            target_file: Path to YAML file relative to repo root.
            content: Dictionary content to merge/write.
            commit_message: Message for the git commit.
            
        Returns:
            Dict with commit_sha and status.
        """
        try:
            repo = self._prepare_repo()
            
            # Optimistic Locking Check
            if not self._is_up_to_date(repo):
                logger.warning("Local repo is behind. Pulling latest.")
                repo.remotes.origin.pull()
            base_sha = repo.head.commit.hexsha
            
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
            
            return self._commit_and_push(repo, target_file, commit_message, base_sha)
            
        except git.GitCommandError as e:
            raise RetryableError(f"Git operation failed: {str(e)}")
        except Exception as e:
            raise RetryableError(f"Unexpected error: {str(e)}")

    @retry_on_retryable(max_attempts=3)
    async def delete_resource(self, target_file: str, keys: List[str], commit_message: str) -> Dict[str, Any]:
        """
        Remove keys from a resource configuration file.
        
        Args:
            target_file: Path to YAML file.
            keys: List of top-level keys to remove.
            commit_message: Message for the git commit.
        """
        try:
            repo = self._prepare_repo()
            if not self._is_up_to_date(repo):
                repo.remotes.origin.pull()
            base_sha = repo.head.commit.hexsha
            
            full_path = Path(self.local_repo_path) / target_file
            if not full_path.exists():
                return {"success": True, "status": "file_not_found", "commit_sha": base_sha}
                
            current_content = {}
            with open(full_path, "r") as f:
                current_content = yaml.safe_load(f) or {}
            
            modified = False
            for key in keys:
                if key in current_content:
                    current_content.pop(key)
                    modified = True
            
            if modified:
                with open(full_path, "w") as f:
                    yaml.dump(current_content, f, default_flow_style=False)
                return self._commit_and_push(repo, target_file, commit_message, base_sha)
            else:
                return {"success": True, "status": "no_change", "commit_sha": base_sha}
                
        except Exception as e:
            raise RetryableError(f"Delete operation failed: {str(e)}")

    async def get_resource(self, target_file: str) -> Optional[Dict[str, Any]]:
        """
        Read the current configuration from the local git repo.
        """
        try:
            repo = self._prepare_repo()
            full_path = Path(self.local_repo_path) / target_file
            if full_path.exists():
                with open(full_path, "r") as f:
                    return yaml.safe_load(f)
            return None
        except Exception as e:
            logger.error(f"Failed to read resource {target_file}: {e}")
            return None

    def _commit_and_push(self, repo: git.Repo, target_file: str, message: str, base_sha: str) -> Dict[str, Any]:
        """Helper to commit and push changes with optimistic locking."""
        if repo.is_dirty(untracked_files=True):
            full_path = str(Path(self.local_repo_path) / target_file)
            repo.index.add([full_path])
            commit = repo.index.commit(message, author=git.Actor(self.username, self.email))
            
            self._push_with_retry(repo)
            
            return {
                "success": True,
                "commit_sha": commit.hexsha,
                "status": "pushed"
            }
        else:
            return {
                "success": True,
                "commit_sha": base_sha,
                "status": "no_change"
            }

    def _prepare_repo(self) -> git.Repo:
        """Clone or load and pull the repository."""
        repo_path = Path(self.local_repo_path)
        
        if self.ssh_key_path:
            os.environ["GIT_SSH_COMMAND"] = f"ssh -i {self.ssh_key_path} -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no"
            
        try:
            if repo_path.exists() and (repo_path / ".git").exists():
                repo = git.Repo(repo_path)
                if self.repo_url not in repo.remotes.origin.url:
                    shutil.rmtree(repo_path)
                    repo = git.Repo.clone_from(self.repo_url, repo_path, branch=self.branch)
                elif not self._is_up_to_date(repo):
                    repo.remotes.origin.pull()
            else:
                if repo_path.exists():
                    shutil.rmtree(repo_path)
                repo = git.Repo.clone_from(self.repo_url, repo_path, branch=self.branch)
                
            with repo.config_writer() as cw:
                cw.set_value("user", "name", self.username)
                cw.set_value("user", "email", self.email)
            return repo
        except Exception as e:
            raise RetryableError(f"Repo preparation failed: {str(e)}")

    def _is_up_to_date(self, repo: git.Repo) -> bool:
        """Check if local HEAD matches remote branch."""
        try:
            repo.remotes.origin.fetch()
            local_sha = repo.head.commit.hexsha
            remote_sha = repo.refs[f"origin/{self.branch}"].commit.hexsha
            return local_sha == remote_sha
        except:
            return False

    def _push_with_retry(self, repo: git.Repo):
        """Push with rebase on conflict."""
        for attempt in range(3):
            try:
                repo.remotes.origin.push()
                return
            except git.GitCommandError:
                if attempt < 2:
                    repo.git.pull("--rebase")
                else:
                    raise RetryableError("Push failed after retries")

    async def health_check(self) -> bool:
        """Check git connectivity."""
        try:
            self._prepare_repo()
            return True
        except:
            return False
