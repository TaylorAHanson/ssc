"""Deterministic quality scoring for a workflow's ``goal``.

The ``goal`` is not a description — at runtime it is the workflow's **entire**
entry in the agent's Capabilities menu (``- {key}: {goal}``, see
``prompts._capabilities_from_db``). It is all the self-service agent has when it
decides which workflow a user's message means; the playbook only arrives after
``get_workflow_instructions``. So a goal has exactly one job: make this workflow
distinguishable from every other line in that menu.

``instructions_quality`` scores the playbook and ``evaluator`` scores the graph.
Neither looks at the goal, which is how a menu ends up full of interchangeable
lines like "Fulfill a campaign request." and three near-identical access
workflows. This rubric checks the properties that actually affect routing:
the goal exists, says something the key doesn't already say, is short enough to
sit in a menu, and does not read like a sibling workflow's line.

Deterministic and side-effect free (no LLM, no DB) — siblings are passed in — so
it can run on every save.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# Words that carry no routing signal: they appear in nearly every goal, so
# counting them would make unrelated workflows look similar and near-duplicates
# look distinct.
_FILLER = frozenset({
    "a", "an", "the", "to", "for", "of", "and", "or", "in", "on", "at", "by",
    "with", "from", "that", "this", "these", "those", "it", "its", "their",
    "they", "them", "so", "is", "are", "be", "been", "can", "will", "should",
    "user", "users", "users'", "help", "helps", "request", "requests",
    "requested", "requesting", "new", "existing", "via", "e", "g", "eg", "etc",
    "want", "wants", "need", "needs", "someone", "workflow", "process",
})

# The auto-generated stub from ``instructions.render_instructions_markdown``
# ("Fulfill a {title} request.") and its close relatives. These are what a
# workflow gets when nobody wrote a goal, and they carry zero routing signal.
_STUB_RE = re.compile(
    r"^(fulfill|handle|process|complete|perform|submit)\s+(a|an|the)?\s*.*?"
    r"\s*requests?\.?$",
    flags=re.IGNORECASE,
)

# Below this a goal cannot be doing its job; above the max it bloats every
# system prompt (the menu carries one line per published workflow).
_MIN_CHARS = 25
_MAX_CHARS = 240
_MAX_SENTENCES = 2

# Content-word overlap (Jaccard) at which two menu lines read as the same
# capability. Tuned against the seeded catalog, where every pair at or above this
# is one a human would also mix up (workspace access vs. provision vs. folder
# creation; catalog-schema-table access vs. data access) and everything below is
# a coincidence of shared vocabulary. Near misses are reported in the summary
# rather than penalized, so a save isn't noisy about unrelated workflows.
_COLLISION_RATIO = 0.40
_NEAR_MISS_RATIO = 0.30


def _tier(score: int) -> str:
    if score >= 85:
        return "excellent"
    if score >= 65:
        return "good"
    if score >= 40:
        return "fair"
    return "poor"


def _content_words(text: str) -> set:
    """Lowercase content words, filler and short tokens dropped."""
    words = re.findall(r"[a-z0-9_]+", (text or "").lower())
    out = set()
    for w in words:
        w = w.rstrip("s") if len(w) > 4 and w.endswith("s") else w
        if len(w) < 3 or w in _FILLER:
            continue
        out.add(w)
    return out


def _overlap(a: set, b: set) -> float:
    """Jaccard similarity, plus containment so a short goal that is wholly
    contained in a longer sibling still counts as a collision."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    jaccard = inter / len(a | b)
    containment = inter / min(len(a), len(b))
    return max(jaccard, containment if containment >= 0.9 else 0.0)


def _sentences(text: str) -> int:
    return len([s for s in re.split(r"[.!?]+\s+|[.!?]+$", text.strip()) if s.strip()])


def score_goal(
    goal: Optional[str],
    *,
    key: str = "",
    name: Optional[str] = None,
    siblings: Iterable[Tuple[str, Optional[str]]] = (),
) -> Dict[str, Any]:
    """Score a workflow goal 0-100 with findings, mirroring the evaluator's shape.

    ``siblings`` are ``(key, goal)`` pairs for the OTHER workflows in the menu —
    the only way to judge whether this line is distinguishable. Pass the
    published ones; a draft nobody can route to yet doesn't compete.

    Returns ``{score, tier, findings: [{severity, message, fix}], summary}``.
    """
    text = (goal or "").strip()
    findings: List[Dict[str, str]] = []

    def add(severity: str, message: str, fix: str = "") -> None:
        findings.append({"severity": severity, "message": message, "fix": fix})

    if not text:
        add(
            "critical",
            "This workflow has no goal, so its line in the agent's Capabilities menu "
            "is empty — the runtime agent has nothing to match a user's request "
            "against and will route to a sound-alike workflow instead.",
            fix=(
                "Write one sentence covering what the user gets, when to pick this "
                "workflow, and how it differs from the nearest lookalike."
            ),
        )
        return {
            "score": 0,
            "tier": "poor",
            "findings": findings,
            "summary": {"chars": 0, "sentences": 0, "is_stub": False, "collisions": []},
        }

    penalty = 0
    words = _content_words(text)

    if _STUB_RE.match(text):
        # The worst possible menu line: it restates the key and nothing else, so
        # routing falls back to guessing. Scored "poor" on its own.
        penalty += 65
        add(
            "high",
            f'"{text}" is the auto-generated stub — it just restates the workflow '
            "name, so the menu line tells the runtime agent nothing it didn't "
            "already know from the key.",
            fix=(
                "Replace it with what the user actually gets and when to choose this "
                "over the neighbouring workflows."
            ),
        )
    else:
        # A goal built only from the key's own words is the same failure wearing
        # different wording (key "tag_change" -> "Change a tag").
        key_words = _content_words(f"{key} {name or ''}")
        if words and words <= key_words:
            penalty += 30
            add(
                "high",
                f'"{text}" only repeats words already in the workflow key '
                f"(`{key}`), so it adds no routing signal.",
                fix="Say what it produces, who it's for, and what it is NOT for.",
            )

    if len(text) < _MIN_CHARS:
        penalty += 20
        add(
            "medium",
            f"The goal is {len(text)} characters — too terse to distinguish this "
            "workflow from a similar one in the menu.",
            fix="One full sentence: what the user gets and when to pick this.",
        )
    elif len(text) > _MAX_CHARS:
        penalty += 10
        add(
            "low",
            f"The goal is {len(text)} characters. It sits in the system prompt for "
            "every conversation, and a paragraph there competes with the other "
            "workflows' lines instead of standing out.",
            fix=(
                "Trim to one or two sentences; move the detail into "
                "instructions_markdown, which the agent fetches when it routes here."
            ),
        )

    if _sentences(text) > _MAX_SENTENCES:
        penalty += 8
        add(
            "low",
            "The goal runs to several sentences; the menu reads best as one line "
            "per capability.",
            fix="Keep the routing sentence here and move procedure into the playbook.",
        )

    # The main event: does this line read like another line in the same menu?
    collisions: List[Dict[str, Any]] = []
    near_misses: List[Dict[str, Any]] = []
    for sib_key, sib_goal in siblings or ():
        if not sib_goal or (sib_key and key and sib_key == key):
            continue
        ratio = _overlap(words, _content_words(sib_goal))
        if sib_goal.strip().lower() == text.lower():
            collisions.append({"key": sib_key, "overlap": 1.0, "identical": True})
        elif ratio >= _COLLISION_RATIO:
            collisions.append({"key": sib_key, "overlap": round(ratio, 2),
                               "identical": False})
        elif ratio >= _NEAR_MISS_RATIO:
            near_misses.append({"key": sib_key, "overlap": round(ratio, 2)})
    collisions.sort(key=lambda c: c["overlap"], reverse=True)
    near_misses.sort(key=lambda c: c["overlap"], reverse=True)

    # A collision is the failure this rubric exists for, so the first one alone has
    # to drag the score under the warning threshold — otherwise the agent reads
    # "good" and ships two interchangeable menu lines. Later ones add less, so a
    # third neighbour can't make the number meaningless.
    for idx, collision in enumerate(collisions[:3]):
        first = idx == 0
        if collision.get("identical"):
            penalty += 70 if first else 25
            add(
                "critical",
                f"This goal is IDENTICAL to `{collision['key']}`'s. The runtime agent "
                "cannot tell the two apart and will pick one arbitrarily.",
                fix=f"State what this does that `{collision['key']}` does not.",
            )
        else:
            penalty += 40 if first else 15
            add(
                "high",
                f"This goal reads almost the same as `{collision['key']}`'s "
                f"({int(collision['overlap'] * 100)}% shared wording). Both lines sit "
                "in the same menu, so the agent has to guess between them.",
                fix=(
                    f"Add the discriminator: name the case this covers and the case "
                    f"`{collision['key']}` covers (e.g. existing vs. new, one asset vs. "
                    f"bulk, self-serve vs. approval-only)."
                ),
            )

    score = max(0, 100 - penalty)
    _rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    findings.sort(key=lambda f: _rank.get(f["severity"], 4))
    return {
        "score": score,
        "tier": _tier(score),
        "findings": findings,
        "summary": {
            "chars": len(text),
            "sentences": _sentences(text),
            "is_stub": bool(_STUB_RE.match(text)),
            "collisions": collisions,
            # Not penalized — shown so the author can see which neighbours share
            # vocabulary before a rewrite drifts into one of them.
            "similar_to": near_misses[:3],
        },
    }


def menu_siblings(
    rows: Sequence[Any], *, exclude_key: str = "",
) -> List[Tuple[str, Optional[str]]]:
    """``(key, goal)`` pairs from workflow rows, for :func:`score_goal`.

    Kept here so callers don't each re-derive "what competes with this line".
    """
    out: List[Tuple[str, Optional[str]]] = []
    for row in rows or ():
        row_key = getattr(row, "key", None)
        if not row_key or row_key == exclude_key:
            continue
        out.append((row_key, getattr(row, "goal", None)))
    return out
