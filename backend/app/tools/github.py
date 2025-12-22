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
        **kwargs
    ) -> Dict[str, Any]:
        """
        Scaffold repository based on form data.
        Expected kwargs from form:
        - domain
        - team_name
        - repo_short_name
        - template
        - visibility
        - members (optional)
        - description (optional)
        """
        domain = kwargs.get("domain")
        team_name = kwargs.get("team_name")
        repo_short_name = kwargs.get("repo_short_name")
        template_choice = kwargs.get("template", "blank")
        visibility = kwargs.get("visibility", "private")
        description = kwargs.get("description", "")
        members_str = kwargs.get("members", "")

        # Construct repository name
        repo_name = f"{domain}-{team_name}-{repo_short_name}".lower().replace(" ", "-")
        
        # Determine owner for templates
        owner = settings.GITHUB_ORG
        if not owner:
            # Fallback to current user if org not set
            async with self.github as gh:
                user_resp = await gh.client.get("/user")
                user_resp.raise_for_status()
                owner = user_resp.json()["login"]

        # Template mapping
        template_map = {
            "data-engineering": f"{owner}/data-engineering-template",
            "databricks-apps": f"{owner}/databricks-apps-template"
        }

        config = {
            "description": description,
            "private": visibility == "private"
        }

        async with self.github as gh:
            if template_choice == "blank" or template_choice not in template_map:
                # Create empty repo
                repo = await gh.create_repo(repo_name, config)
            else:
                # Create from template
                template_path = template_map[template_choice]
                repo = await gh.create_from_template(template_path, repo_name, config)
            
            # Add members
            if members_str:
                # Split by newline or comma and clean
                import re
                members = [m.strip() for m in re.split(r'[\n,]', members_str) if m.strip()]
                for member in members:
                    # In a real scenario, you'd map email to GH username
                    # For now, we'll try to add them directly (assuming username or email works if GH supports it, 
                    # but usually it's username/login)
                    try:
                        await gh.set_permissions(repo_name, member, "push")
                    except Exception as e:
                        # Log error but don't fail the whole creation
                        print(f"Failed to add member {member}: {e}")

        return {
            "repo_url": repo.get("html_url"),
            "repo_name": repo_name,
            "status": "completed"
        }

