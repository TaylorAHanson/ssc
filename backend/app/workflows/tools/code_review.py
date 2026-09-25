"""Code review workflow tools."""
import logging
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from app.tools.mcp import tool
from app.workflows.tools import _common

logger = logging.getLogger(__name__)


class ReviewAppCodeInput(BaseModel):
    repo: str = Field(
        ...,
        description="The app's GitHub repository in any form the requester gave: a repo, "
                    "branch/directory, file, PR or commit URL, 'owner/repo', or a bare repo name.",
    )
    ref: Optional[str] = Field(
        default=None,
        description="Branch, tag or commit to review when the repo reference doesn't pin one. "
                    "Defaults to the default branch.",
    )
    app_path: Optional[str] = Field(
        default=None,
        description="Directory of the app inside the repo (monorepos), when the reference doesn't include it.",
    )


@tool(
    name="review_databricks_app_code",
    args_schema=ReviewAppCodeInput,
    side_effect_class="read",
    friendly_label="Reviewing the app's code",
    friendly_completion_label="Reviewed the app's code",
    description=(
        "Security-review a Databricks App's source on GitHub before production. Pins a commit, "
        "checks whose identity reads data (on-behalf-of-user vs. the app's service principal) "
        "and for committed secrets, then has an AI reviewer with read-only access give a "
        "recommendation (approve / approve_with_notes / needs_discussion) with a confidence "
        "score. Returns report_markdown, which is shown to approvers on later approval steps. "
        "Never runs the app's code."
    ),
)
async def review_databricks_app_code(
    repo: str,
    ref: Optional[str] = None,
    app_path: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    from app.services.app_code_review import review_app_code

    provider = _common._get_github_provider()
    async with provider as github:
        return await review_app_code(github, repo, ref=ref, app_path=app_path)
