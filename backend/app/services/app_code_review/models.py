"""Shared types and vocabularies for the app code review."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

# Ordered least -> most severe; the final recommendation is the max of the
# deterministic floor and the reviewer's view (the reviewer can only escalate).
RECOMMENDATIONS = ["approve", "approve_with_notes", "needs_discussion"]

SEVERITIES = ["minor", "concern", "blocker"]

# Whose identity the app uses to read governed (Unity Catalog) data, ordered
# by risk. "App resources" are what the app's own service principal is meant
# to reach: its Lakebase database for app state, serving endpoints, secrets.
IDENTITIES = [
    "no_databricks_access",
    "obo_only",
    "sp_app_resources_only",
    "mixed",
    "unknown",
    "sp_with_data_access",
]
# The readings that need a person: the service principal reads governed data,
# or nobody could tell. Between the others the reviewer's reading wins, since
# it actually traced the code; into or out of these, the riskier reading wins.
RISKY_IDENTITIES = {"unknown", "sp_with_data_access"}

# The controls every review reports a status for (the rubric defines each).
CONTROLS = [
    "platform", "data_governance", "identity", "network", "secrets",
    "secure_sdlc", "logging", "file_uploads",
]
CONTROL_LABELS = {
    "platform": "Approved platform & environments",
    "data_governance": "Unity Catalog governance",
    "identity": "OBO / service principal use",
    "network": "Network & outbound access",
    "secrets": "Secrets management",
    "secure_sdlc": "Secure coding",
    "logging": "Logging & monitoring",
    "file_uploads": "File uploads",
}
# Findings are filed under a control, so they line up with the checklist, or
# under one of these. ``sp_data_access`` is reserved for the service principal
# reaching governed data (it alone sets the identity), and ``authorization`` is
# who may call what, which the controls list leaves implicit.
FINDING_CATEGORIES = CONTROLS + ["sp_data_access", "authorization", "prompt_injection", "other"]

# met / gap come from the code; confirm = can't be seen in code, the admin
# checks it (never lowers the recommendation on its own).
CONTROL_STATUSES = ["met", "gap", "confirm", "not_applicable"]

IDENTITY_LABELS = {
    "no_databricks_access": "No Databricks access detected",
    "obo_only": "On-behalf-of-user only",
    "sp_app_resources_only": "Service principal for app resources only",
    "mixed": "User token for data, service principal for app resources",
    "unknown": "Unclear",
    "sp_with_data_access": "Service principal reads governed data",
}


def more_severe(order: List[str], a: str, b: str) -> str:
    """The later of ``a`` and ``b`` in ``order`` (unknown values count as least severe)."""
    rank = {v: i for i, v in enumerate(order)}
    return a if rank.get(a, -1) >= rank.get(b, -1) else b


@dataclass
class Finding:
    severity: str
    category: str
    title: str
    why_it_matters: str = ""
    file: Optional[str] = None
    line: Optional[int] = None
    evidence: str = ""
    remediation: str = ""
    # "pre-scan" (deterministic) or "reviewer" (LLM).
    source: str = "pre-scan"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Signal:
    """One place in the code that says something about identity or data access."""
    kind: str
    file: str
    line: int
    snippet: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ResolvedSource:
    """Exactly which code is being reviewed."""
    full_name: str
    sha: str
    ref_label: str
    subpath: str = ""
    html_url: str = ""


@dataclass
class Snapshot:
    """Text files of the reviewed tree, held in memory (never written to disk)."""
    files: Dict[str, str] = field(default_factory=dict)
    # (path, reason) for files left out: size, binary, excluded dir, budget.
    skipped: List[tuple] = field(default_factory=list)
    # True when the total-size budget, not just per-file rules, dropped files.
    budget_exhausted: bool = False
