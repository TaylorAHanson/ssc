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
            branch: Main branch to operate on (default: main).
            config: Additional configuration (local_repo_path, etc.).
        """
        super().__init__(config)
        self.repo_url = repo_url
        self.main_branch = branch
        self.local_repo_path = self.config.get("local_repo_path", "/tmp/infra-repo")
        self.username = self.config.get("git_username", "Ops Bot")
        self.email = self.config.get("git_email", "ops-bot@example.com")
        self.ssh_key_path = self.config.get("ssh_key_path")
        
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
        
        if self.ssh_key_path:
            os.environ["GIT_SSH_COMMAND"] = f"ssh -i {self.ssh_key_path} -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no"
            
        try:
            if repo_path.exists() and (repo_path / ".git").exists():
                repo = git.Repo(repo_path)
                if self.repo_url not in repo.remotes.origin.url:
                    shutil.rmtree(repo_path)
                    repo = git.Repo.clone_from(self.repo_url, repo_path, branch=self.main_branch)
                # Don't pull here, let methods handle branching/pulling
            else:
                if repo_path.exists():
                    shutil.rmtree(repo_path)
                repo = git.Repo.clone_from(self.repo_url, repo_path, branch=self.main_branch)
                
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
