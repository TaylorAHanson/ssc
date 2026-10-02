"""
Tool to look up Databricks preview features (Beta / Public Preview) the app tracks.
"""
from typing import Any, Dict

from pydantic import BaseModel, Field

from app.tools.mcp import tool


class FindPreviewFeaturesInput(BaseModel):
    query: str = Field(
        ...,
        min_length=2,
        description="The feature the user named, e.g. 'ai enrich', 'ABAC on views', 'Jira connector'.",
    )
    limit: int = Field(default=5, ge=1, le=10, description="Max matches to return.")


@tool(
    name="find_preview_features",
    description=(
        "Look up Databricks Beta / Public Preview features tracked on the Preview Features page. "
        "Returns what each match does (description, announcement, docs and release-note links), "
        "its phase and scope (workspace or account), and per target workspace whether it's listed, "
        "already on, has an open request, and can be requested on or off. Also returns related "
        "previews and any match that's now generally available. Use it before a "
        "preview_feature_request, and use its `feature` value as that workflow's feature parameter."
    ),
    args_schema=FindPreviewFeaturesInput,
    feature_flag="preview_features",
    friendly_label="Looking up preview features",
    friendly_completion_label="Looked up preview features",
)
def find_preview_features(query: str, limit: int = 5, **kwargs) -> Dict[str, Any]:
    from app.core.workspaces import get_target_workspaces
    from app.db.session import get_lakebase_session
    from app.services.preview_features.search import find

    db = get_lakebase_session()
    try:
        return find(db, query, [w.name for w in get_target_workspaces()], limit=limit)
    finally:
        db.close()
