"""
``render_chart`` tool: turn the conversation's latest data answer into a chart.

Databricks Genie returns *data*, never a chart — it explicitly tells callers to
visualize the results themselves. This tool lets the agent satisfy natural-
language charting requests ("graph that as a line by month", "show it as a pie")
by emitting a compact **chart directive** (mark + field encodings). The chart
itself is drawn client-side by the chat UI, which binds the directive to the
most recent dataset (the rows Genie already returned) — so we don't re-query and
don't ship row data through the LLM.

The tool is deliberately thin and non-mutating: it validates the requested
encoding and returns it. The UI owns rendering (Vega-Lite via vega-embed) and the
interactive "re-graph" controls, so the same directive the agent produces is what
a user can then tweak by hand.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.tools.mcp import tool

logger = logging.getLogger(__name__)

# Kept in lockstep with the frontend ``ChartMark`` / ``ChartAggregate`` unions
# (src/lib/charting.ts). Validation here is the single source of truth the agent
# is held to; the UI degrades gracefully but we never want to emit a junk mark.
_MARKS = {"bar", "line", "area", "point", "arc"}
_AGGREGATES = {"sum", "mean", "median", "min", "max", "count", "none"}

_DESCRIPTION = """\
Visualize the most recent data answer as a chart, or re-graph it a different way. \
Use this when the user asks to chart / plot / graph data, or to change an existing \
chart ("make it a line", "show it as a pie by region", "stack by category").

Databricks Genie returns data but never a chart, so call this AFTER a data answer \
(e.g. from ask_your_data) to turn those rows into a visualization. You only specify \
HOW to chart it — the chart binds to the rows already returned in this conversation, \
so you do NOT need to pass the data yourself.

Specify:
- mark: bar | line | area | point (scatter) | arc (pie)
- x: the column for the x axis (or the category for a pie)
- y: the column for the y axis / measure (or the value for a pie)
- color: optional column to split series by color / slices
- aggregate: how to combine the measure when grouping (sum, mean, median, min, max, \
count, or none for raw values)

Pick columns by their names as they appear in the data answer. Prefer 'line' for \
trends over time, 'bar' for category comparisons, 'point' for two numeric measures, \
and 'arc' for parts-of-a-whole. The user can fine-tune the result with on-chart \
controls afterward.\
"""


class RenderChartInput(BaseModel):
    """Schema for the ``render_chart`` tool — a compact Vega-Lite-ish encoding."""

    mark: str = Field(
        default="bar",
        description="Chart type: one of bar, line, area, point (scatter), arc (pie).",
    )
    x: Optional[str] = Field(
        default=None,
        description="Column for the x axis (or the category dimension for a pie).",
    )
    y: Optional[str] = Field(
        default=None,
        description="Column for the y axis / measure (or the value for a pie).",
    )
    color: Optional[str] = Field(
        default=None,
        description="Optional column to break the series out by color / pie slices.",
    )
    aggregate: str = Field(
        default="sum",
        description=(
            "How to aggregate the measure when grouping: sum, mean, median, min, "
            "max, count, or none (plot raw values)."
        ),
    )
    title: Optional[str] = Field(default=None, description="Optional chart title.")
    data: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description=(
            "Optional explicit rows (list of {column: value} objects) to chart. "
            "Usually OMIT this — the chart binds to the data already returned in the "
            "conversation. Only pass it when charting data you computed yourself."
        ),
    )


@tool(
    name="render_chart",
    description=_DESCRIPTION,
    args_schema=RenderChartInput,
    feature_flag="ask_your_data",
    side_effect_class="read",
    friendly_label="Building chart...",
    friendly_completion_label="Chart ready",
)
async def render_chart(
    mark: str = "bar",
    x: Optional[str] = None,
    y: Optional[str] = None,
    color: Optional[str] = None,
    aggregate: str = "sum",
    title: Optional[str] = None,
    data: Optional[List[Dict[str, Any]]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Validate the requested encoding and return a chart directive for the UI.

    Returns ``{ok, chart_directive, summary}``; the chat surface renders the chart
    and binds it to the conversation's most recent dataset (unless ``data`` is
    supplied). Invalid marks/aggregates are corrected to safe defaults with a note
    rather than failing the turn.
    """
    notes: List[str] = []

    mark_norm = (mark or "bar").strip().lower()
    if mark_norm in ("scatter", "circle", "square"):
        mark_norm = "point"
    if mark_norm in ("pie", "donut", "doughnut"):
        mark_norm = "arc"
    if mark_norm not in _MARKS:
        notes.append(f"Unknown mark '{mark}', defaulted to 'bar'.")
        mark_norm = "bar"

    agg_norm = (aggregate or "sum").strip().lower()
    if agg_norm in ("avg", "average"):
        agg_norm = "mean"
    if agg_norm not in _AGGREGATES:
        notes.append(f"Unknown aggregate '{aggregate}', defaulted to 'sum'.")
        agg_norm = "sum"

    directive: Dict[str, Any] = {"mark": mark_norm, "aggregate": agg_norm}
    if x:
        directive["x"] = x.strip()
    if y:
        directive["y"] = y.strip()
    if color:
        directive["color"] = color.strip()
    if title:
        directive["title"] = title.strip()

    result: Dict[str, Any] = {
        "ok": True,
        "chart_directive": directive,
        "summary": _summarize(directive),
    }
    if data:
        # Explicit, self-contained data the agent computed. Cap to keep the
        # payload sane; the UI charts what it's given.
        result["data"] = data[:5000]
    if notes:
        result["notes"] = notes

    logger.info("render_chart directive=%s (explicit_data=%s)", directive, bool(data))
    return result


def _summarize(directive: Dict[str, Any]) -> str:
    mark = directive.get("mark", "bar")
    x = directive.get("x")
    y = directive.get("y")
    if mark == "arc":
        cat = directive.get("color") or x
        return f"Pie chart of {y or 'count'} by {cat or 'category'}."
    bits = f"{mark.capitalize()} chart"
    if y:
        bits += f" of {y}"
    if x:
        bits += f" by {x}"
    if directive.get("color"):
        bits += f", colored by {directive['color']}"
    return bits + "."
