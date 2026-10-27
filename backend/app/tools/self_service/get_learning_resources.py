from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field

from app.services import learning_resources
from app.tools.mcp import tool


class GetLearningResourcesInput(BaseModel):
    kind: Literal["all", "training", "community"] = Field(
        "all",
        description=(
            "'training' for courses, 'community' for the curated links (documentation, "
            "learning sites, tools), or 'all' (default)."
        ),
    )
    persona: Optional[str] = Field(
        None,
        description=(
            "Narrow training to one audience, e.g. 'Business User', 'Data Analyst', "
            "'Data Engineer'. Match the user's role in the conversation."
        ),
    )
    topic: Optional[str] = Field(
        None, description="A word to filter courses and links by, e.g. 'SQL', 'dashboards', 'governance'."
    )


@tool(
    name="get_learning_resources",
    description=(
        "Look up the training courses and community links this organization offers. Use it "
        "when the user wants to learn something, asks what training to take, or wants "
        "documentation or somewhere to ask other people. It returns only what is switched on "
        "and currently configured; if it returns nothing, say there is nothing to point to "
        "rather than naming a course or link from memory. For upcoming sessions use "
        "search_events instead."
    ),
    args_schema=GetLearningResourcesInput,
    is_lookup=True,
    # Offered only while the Training or Community Links page is on and has content, so
    # turning a page off in Admin -> Settings stops the assistant sending people to it.
    available_when=learning_resources.learning_available,
    friendly_label="Looking up training and community resources...",
)
def get_learning_resources(
    kind: str = "all", persona: Optional[str] = None, topic: Optional[str] = None
) -> Dict[str, Any]:
    return learning_resources.learning_resources(kind=kind, persona=persona, topic=topic)
