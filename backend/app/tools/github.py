"""
GitHub tools.
"""
from typing import Dict, Any
from app.tools.base import BaseTool
from app.providers.github import GitHubProvider
from app.core.config import settings


class ScaffoldGitHubRepoTool(BaseTool):
    """Scaffold GitHub repository from template."""
    
    def __init__(self):
        """Initialize providers from settings."""
        self.github = GitHubProvider(
            token=settings.GITHUB_TOKEN or "",
            org=settings.GITHUB_ORG or ""
        )
    
    async def execute(
        self,
        name: str,
        template: str,
        config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Scaffold repository from template."""
        # Create from template
        repo = await self.github.create_from_template(template, name, config)
        
        # Set permissions
        if config.get("collaborators"):
            for collaborator, permission in config["collaborators"].items():
                await self.github.set_permissions(name, collaborator, permission)
        
        # Run additional setup commands if needed
        if config.get("setup_commands"):
            for command in config["setup_commands"]:
                await self.github.run_shell_command(command, cwd=f"/tmp/{name}")
        
        return {
            "repo_url": repo.get("html_url"),
            "status": "completed"
        }

