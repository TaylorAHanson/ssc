"""Compact ``state_context`` summaries for list views.

A request's ``state_context`` can be enormous — a Sentinel run holds every
``violation`` and every ``checks`` (pass/fail) evaluation, hundreds of MB. List
screens only need aggregate counts, so we persist a small ``state_summary``
alongside the full blob (see ``RequestModel.state_summary``) and read that in the
list path — the big column is never fetched. This module owns the (single)
definition of what that summary contains so the write path (Sentinel persist) and
the read path (requests API) can't drift.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

#: Large, list-view-irrelevant arrays dropped from the summary. These grow
#: unbounded (a Sentinel run's per-violation records and its ``checks`` checklist
#: — EVERY pass/fail evaluation, which dwarfs violations).
_HEAVY_METADATA_KEYS = ("violations", "checks", "resources", "scan_results", "assets")

#: List fields the rows DO read; kept even though they're arrays (bounded by the
#: workspace count, so small).
_KEEP_LIST_KEYS = ("workspaces_scanned", "workspace_failures")

#: Any other top-level array longer than this is replaced with a compact marker —
#: a backstop so a newly-added big field can't silently bloat the summary.
_MAX_SUMMARY_LIST = 250


def summarize_state_context(state_context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Trim heavy arrays out of a request's ``state_context`` for list views.

    Preserves the small summary fields list rows use (``scan_stats``,
    ``workspace_failures``, ``workspaces_scanned``, scalars) and drops the large
    arrays. Ensures a ``scan_stats.violation_count`` survives so a Sentinel row
    still shows the right count without shipping every violation record.
    """
    if not isinstance(state_context, dict):
        return {}
    meta = dict(state_context)

    violations = meta.get("violations")
    if isinstance(violations, list):
        stats = dict(meta.get("scan_stats") or {})
        if "violation_count" not in stats:
            # Mirror the UI's per-rule count: sum violation_reasons (>=1 each).
            stats["violation_count"] = sum(
                (len(v.get("violation_reasons") or []) or 1)
                for v in violations
                if isinstance(v, dict)
            )
        meta["scan_stats"] = stats

    for key in _HEAVY_METADATA_KEYS:
        meta.pop(key, None)

    # Backstop: drop any other oversized top-level array (keep the small
    # list-view fields) so a future big field can't re-bloat the list payload.
    for key, val in list(meta.items()):
        if key in _KEEP_LIST_KEYS:
            continue
        if isinstance(val, list) and len(val) > _MAX_SUMMARY_LIST:
            meta[key] = {"_omitted": True, "count": len(val)}
    return meta
