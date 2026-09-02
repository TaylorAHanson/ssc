"""
Advisory AI Agent Review for Unity Catalog Tag Changes.

Uses the platform's Agent LLM / AI Gateway to analyze the proposed tag changes,
evaluating semantic consistency, access-control impacts, potential risks, and
author questions.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.workflows.tag_lint import LintFinding
from app.workflows.tag_plan import TagPlan
from app.workflows.tag_risk import RiskReport

logger = logging.getLogger(__name__)

_FENCE = "=" * 8
_JSON_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class ReviewConcern:
    severity: str  # "info", "low", "medium", "high"
    object: str
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "severity": self.severity,
            "object": self.object,
            "message": self.message,
        }


@dataclass
class AgentReviewResult:
    available: bool = False
    reason: str = ""
    model: str = ""
    summary: str = ""
    concerns: List[ReviewConcern] = field(default_factory=list)
    questions: List[str] = field(default_factory=list)
    risk_agreement: str = ""  # "agree", "lower", "higher"
    rationale: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "available": self.available,
            "reason": self.reason,
            "model": self.model,
            "summary": self.summary,
            "concerns": [c.to_dict() for c in self.concerns],
            "questions": self.questions,
            "risk_agreement": self.risk_agreement,
            "rationale": self.rationale,
        }


SYSTEM_PROMPT = """You review requests that change Unity Catalog governance tags on tables and views.
You advise the human administrator; you do not approve or block anything.

Judge the change on:
1. Whether the tag values look intentional and internally consistent.
2. Whether access-control keys (access_group, approver_group) are changing in a way that deserves attention.
3. Whether removals lose information that will be hard to reconstruct.
4. Consistency across tables in the dataset.

The syntax, the policy rules, and the tag limits have already been validated by separate deterministic checks. Do not re-report them.

Reply strictly with a single JSON object and nothing else:
{
  "summary": "one or two concise sentences summarizing the change and risk posture",
  "concerns": [{"severity": "info|low|medium|high", "object": "fqn or empty", "message": "what and why"}],
  "questions": ["optional question for the requester"],
  "risk_agreement": "agree|lower|higher",
  "rationale": "one sentence on why you agree/disagree with the computed risk score"
}
"""


def _fenced(label: str, body: str, limit: int = 10000) -> str:
    text = (body or "").replace(_FENCE, "").strip()
    if len(text) > limit:
        text = text[:limit] + "\n[truncated]"
    return f"{_FENCE} BEGIN DATA: {label} {_FENCE}\n{text or '(empty)'}\n{_FENCE} END DATA {_FENCE}"


async def request_agent_review(
    dataset_name: str,
    plan: TagPlan,
    risk_report: RiskReport,
    lint_findings: Sequence[LintFinding],
) -> AgentReviewResult:
    """Request an advisory LLM review for the proposed tag changes."""
    try:
        from app.model_serving.agent_llm import AgentLLMClient

        client = AgentLLMClient()
    except Exception as exc:
        logger.info(f"Agent review unavailable: {exc}")
        return AgentReviewResult(available=False, reason=str(exc))

    # Build prompt context
    diff_lines = []
    for diff in plan.diffs.values():
        if diff.changed_keys:
            diff_lines.append(f"Table: {diff.table} ({diff.object_type})")
            for k in diff.changed_keys:
                old_val = diff.before.get(k, "<unset>")
                new_val = diff.after.get(k, "<removed>")
                diff_lines.append(f"  - {k}: {old_val} -> {new_val}")
    diff_text = "\n".join(diff_lines)

    statements_text = "\n".join(p.sql for p in plan.actionable if p.sql)
    risk_text = f"Score: {risk_report.score}/100 ({risk_report.band.upper()})\nFactors: " + ", ".join(
        f"{f.label}: +{f.rounded}" for f in risk_report.contributing
    )
    lint_text = "\n".join(
        f"[{f.severity.upper()}] {f.fqn} ({f.key}): {f.message}"
        for f in lint_findings
    )

    user_content = "\n\n".join([
        f"Dataset: {dataset_name}",
        _fenced("Tag Diffs", diff_text),
        _fenced("SQL Statements to Run", statements_text),
        _fenced("Deterministic Risk Report", risk_text),
        _fenced("Hygiene / Typo Check Findings", lint_text),
        "Provide your JSON review.",
    ])

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    try:
        resp = await client.generate_response(messages=messages, temperature=0.0, max_tokens=1000)
        content = resp.get("content", "")
        # Extract JSON
        clean = _JSON_FENCE_RE.sub("", content).strip()
        start = clean.find("{")
        end = clean.rfind("}")
        if start == -1 or end <= start:
            return AgentReviewResult(available=False, reason="Model response did not contain valid JSON.")
        
        parsed = json.loads(clean[start : end + 1])
        concerns = []
        for c in parsed.get("concerns", []):
            if isinstance(c, dict) and c.get("message"):
                sev = str(c.get("severity", "info")).lower()
                if sev not in ("info", "low", "medium", "high"):
                    sev = "info"
                concerns.append(ReviewConcern(severity=sev, object=str(c.get("object", "")), message=str(c["message"])))

        return AgentReviewResult(
            available=True,
            model=getattr(client, "endpoint_name", "AI Gateway"),
            summary=str(parsed.get("summary", "")),
            concerns=concerns,
            questions=[str(q) for q in parsed.get("questions", []) if q],
            risk_agreement=str(parsed.get("risk_agreement", "")).lower(),
            rationale=str(parsed.get("rationale", "")),
        )
    except Exception as exc:
        logger.warning(f"Agent review failed: {exc}")
        return AgentReviewResult(available=False, reason=str(exc))
