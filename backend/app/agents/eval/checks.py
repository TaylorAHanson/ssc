"""Deterministic checks for an assistant test case.

Cheap, repeatable assertions that run before (and independently of) the LLM judge:
which tools were or were not called, how often, and what the final answer may or
may not say. They are what makes a before/after comparison trustworthy, because the
judge can flip between runs and these cannot.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from app.agents.eval.library import EvalCase

# Characters of the answer quoted back in a failure, enough to see why without
# turning the report into a transcript.
_QUOTE = 120


def _check(name: str, ok: bool, detail: str) -> Dict[str, Any]:
    return {"check": name, "ok": ok, "detail": detail}


def _arg_text(value: Any) -> str:
    """An argument as the text a pattern is matched against."""
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(value, default=str)


def _tool_arg_checks(
    case: EvalCase, tool_calls: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for exp in case.expect_tool_args:
        on_turn = f" on turn {exp.turn}" if exp.turn else ""
        calls = [
            c for c in tool_calls
            if c.get("name") == exp.tool and (exp.turn is None or c.get("turn") == exp.turn)
        ]
        for pattern, wanted in ((exp.matches, True), (exp.not_matches, False)):
            if pattern is None:
                continue
            verb = "matches" if wanted else "avoids"
            label = f"{exp.tool} {exp.arg} {verb} /{pattern}/{on_turn}"
            if not calls:
                results.append(_check(label, False, f"{exp.tool} was not called{on_turn}"))
                continue
            bad = [
                _arg_text((c.get("arguments") or {}).get(exp.arg))
                for c in calls
                if bool(re.search(pattern, _arg_text((c.get("arguments") or {}).get(exp.arg)), re.IGNORECASE | re.MULTILINE))
                is not wanted
            ]
            if bad:
                shown = (bad[0] or "<not passed>")[:_QUOTE]
                results.append(_check(label, False, f"{len(bad)} of {len(calls)} call(s) failed: {shown!r}"))
            else:
                results.append(_check(label, True, f"{len(calls)} call(s) ok"))
    return results


def evaluate_checks(
    case: EvalCase,
    tool_names: List[str],
    answer: str,
    tool_calls: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Return one ``{check, ok, detail}`` entry per assertion on the case.

    ``tool_calls`` carries each call's arguments and turn; ``expect_tool_args`` needs
    it, and without it those assertions fail rather than quietly passing.
    """
    results: List[Dict[str, Any]] = []
    called = set(tool_names)

    for req in case.expect_tools:
        options = [req] if isinstance(req, str) else list(req)
        hit = [t for t in options if t in called]
        label = " or ".join(options)
        results.append(
            _check(
                f"calls {label}",
                bool(hit),
                f"called {hit[0]}" if hit else f"none of {options} was called (called: {sorted(called) or 'nothing'})",
            )
        )

    for name in case.forbid_tools:
        results.append(
            _check(
                f"does not call {name}",
                name not in called,
                "not called" if name not in called else f"called {tool_names.count(name)}x",
            )
        )

    for name, limit in case.tool_limits.items():
        count = tool_names.count(name)
        results.append(
            _check(f"calls {name} at most {limit}x", count <= limit, f"called {count}x")
        )

    results.extend(_tool_arg_checks(case, tool_calls or []))

    if case.max_tool_calls is not None:
        total = len(tool_names)
        results.append(
            _check(
                f"uses at most {case.max_tool_calls} tool calls",
                total <= case.max_tool_calls,
                f"{total} calls" + ("" if total <= case.max_tool_calls else f": {', '.join(tool_names)}"),
            )
        )

    for pattern in case.answer_matches:
        found = re.search(pattern, answer or "", re.IGNORECASE | re.MULTILINE)
        results.append(
            _check(
                f"answer matches /{pattern}/",
                bool(found),
                "found" if found else "not found in the answer",
            )
        )

    for pattern in case.answer_not_matches:
        found = re.search(pattern, answer or "", re.IGNORECASE | re.MULTILINE)
        if found:
            start = max(0, found.start() - 40)
            snippet = (answer or "")[start:start + _QUOTE].replace("\n", " ")
            results.append(_check(f"answer avoids /{pattern}/", False, f"found: ...{snippet}..."))
        else:
            results.append(_check(f"answer avoids /{pattern}/", True, "absent"))

    return results


def failed_checks(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [r for r in results if not r["ok"]]
