"""
Typo and hygiene checks over proposed Unity Catalog tag values.

Compares proposed tag values against the existing tag vocabulary in the catalog
to flag suspected typos (e.g. ``sales-egn`` vs ``sales-eng``), case collisions,
non-ASCII characters, stray whitespace, and unsetting non-existent keys.
"""
from __future__ import annotations

import difflib
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from app.workflows.tag_plan import ObjectDiff, TagPlan, TagVocabulary
from app.workflows.tag_policy import TagPolicy

WARNING = "warning"
INFO = "info"

NEAR_MISS_VALUE = "NEAR_MISS_VALUE"
CASE_COLLISION = "CASE_COLLISION"
NOVEL_VALUE = "NOVEL_VALUE"
NEAR_MISS_KEY = "NEAR_MISS_KEY"
WHITESPACE = "WHITESPACE"
NON_ASCII = "NON_ASCII"
UNSET_KEY_ABSENT = "UNSET_KEY_ABSENT"


@dataclass(frozen=True)
class LintConfig:
    value_similarity: float = 0.85
    key_similarity: float = 0.80
    min_corpus_uses: int = 2
    max_suggestions: int = 3


@dataclass(frozen=True)
class Suggestion:
    value: str
    uses: int

    def to_dict(self) -> Dict[str, Any]:
        return {"value": self.value, "uses": self.uses}


@dataclass(frozen=True)
class LintFinding:
    code: str
    severity: str
    fqn: str
    key: str
    message: str
    value: str = ""
    suggestions: Tuple[Suggestion, ...] = ()

    @property
    def is_warning(self) -> bool:
        return self.severity == WARNING

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "fqn": self.fqn,
            "key": self.key,
            "value": self.value,
            "message": self.message,
            "suggestions": [s.to_dict() for s in self.suggestions],
        }


def _similar(candidate: str, options: Iterable[str], threshold: float) -> List[Tuple[str, float]]:
    scored = []
    for option in options:
        if option == candidate:
            continue
        ratio = difflib.SequenceMatcher(None, candidate, option).ratio()
        if ratio >= threshold:
            scored.append((option, ratio))
    scored.sort(key=lambda item: (-item[1], item[0]))
    return scored


def _describe_non_ascii(text: str) -> str:
    seen: List[str] = []
    for ch in text:
        if ord(ch) < 128:
            continue
        name = unicodedata.name(ch, "")
        label = f"U+{ord(ch):04X}" + (f" {name}" if name else "")
        if label not in seen:
            seen.append(label)
    return ", ".join(seen)


def run_lint_checks(
    plan: TagPlan,
    vocabulary: TagVocabulary,
    policy: TagPolicy,
    config: Optional[LintConfig] = None,
) -> List[LintFinding]:
    """Run hygiene and typo checks across all planned tag modifications."""
    cfg = config or LintConfig()
    findings: List[LintFinding] = []
    seen: Set[Tuple[str, str, str, str]] = set()

    def add(finding: LintFinding) -> None:
        identity = (finding.code, finding.fqn, finding.key, finding.value)
        if identity in seen:
            return
        seen.add(identity)
        findings.append(finding)

    for norm_key, diff in plan.diffs.items():
        table = diff.table

        # Check values to be set/updated
        for key in diff.changed_keys:
            val = diff.after.get(key)
            if val is not None:
                # 1. Whitespace check
                if val != val.strip() or "  " in val:
                    clean_val = " ".join(val.split())
                    add(
                        LintFinding(
                            code=WHITESPACE,
                            severity=WARNING,
                            fqn=table,
                            key=key,
                            value=val,
                            message=f"Value for '{key}' has leading, trailing, or doubled whitespace: '{val}'.",
                            suggestions=(Suggestion(clean_val, 0),) if clean_val else (),
                        )
                    )

                # 2. Non-ASCII check
                if any(ord(ch) > 127 for ch in val):
                    add(
                        LintFinding(
                            code=NON_ASCII,
                            severity=WARNING,
                            fqn=table,
                            key=key,
                            value=val,
                            message=f"Value for '{key}' contains non-ASCII character(s) ({_describe_non_ascii(val)}), which are invisible in diffs.",
                        )
                    )

                # 3. Vocabulary checks (if vocabulary available)
                if vocabulary.available:
                    known = vocabulary.known_values(key)
                    if val not in known:
                        # Case collision
                        collisions = [v for v in known if v.lower() == val.lower()]
                        if collisions:
                            add(
                                LintFinding(
                                    code=CASE_COLLISION,
                                    severity=WARNING,
                                    fqn=table,
                                    key=key,
                                    value=val,
                                    message=f"Value '{val}' for '{key}' differs only by case from an established value in use.",
                                    suggestions=tuple(
                                        Suggestion(v, known[v]) for v in sorted(collisions)[: cfg.max_suggestions]
                                    ),
                                )
                            )
                        else:
                            # Near-miss typo check against established values
                            established = {v: n for v, n in known.items() if n >= cfg.min_corpus_uses}
                            near = _similar(val, established, cfg.value_similarity)
                            if near:
                                add(
                                    LintFinding(
                                        code=NEAR_MISS_VALUE,
                                        severity=WARNING,
                                        fqn=table,
                                        key=key,
                                        value=val,
                                        message=f"Value '{val}' for '{key}' is very close to an established value already used in the catalog.",
                                        suggestions=tuple(
                                            Suggestion(v, established[v]) for v, _ in near[: cfg.max_suggestions]
                                        ),
                                    )
                                )
                            else:
                                # Novel value
                                add(
                                    LintFinding(
                                        code=NOVEL_VALUE,
                                        severity=INFO,
                                        fqn=table,
                                        key=key,
                                        value=val,
                                        message=f"Value '{val}' for '{key}' is not used anywhere else yet in this catalog.",
                                    )
                                )

                # 4. Key typo check against policy keys
                if key not in policy.known_keys and not policy.is_reserved(key):
                    near_keys = _similar(key, policy.known_keys, cfg.key_similarity)
                    if near_keys:
                        add(
                            LintFinding(
                                code=NEAR_MISS_KEY,
                                severity=WARNING,
                                fqn=table,
                                key=key,
                                value=val,
                                message=f"Tag key '{key}' is very close to a key declared in the governance tag policy.",
                                suggestions=tuple(
                                    Suggestion(name, 0) for name, _ in near_keys[: cfg.max_suggestions]
                                ),
                            )
                        )

        # 5. Check removed keys
        for key in diff.removed_keys:
            if key not in diff.before:
                add(
                    LintFinding(
                        code=UNSET_KEY_ABSENT,
                        severity=WARNING,
                        fqn=table,
                        key=key,
                        value="",
                        message=f"Unsets tag '{key}', which '{table}' does not currently have.",
                    )
                )

    findings.sort(key=lambda f: (f.severity != WARNING, f.fqn, f.key, f.code))
    return findings
