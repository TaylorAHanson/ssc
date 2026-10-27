"""The assistant test library: scripted conversations plus what should happen.

This mirrors the Tests tab in workflow authoring (a plain-English question and a
plain-English expected outcome, judged by an LLM) but for the assistant as a whole
rather than one workflow. A case lives as data in ``cases/*.yaml`` so the library
is reviewable in a pull request and the same cases can be re-run after every
prompt, tool or model change.

A case adds two things the studio does not have, because a judge alone is too
soft to compare two runs against each other:

* **Deterministic checks**: tools that must or must not be called, a cap on how
  often a tool may run, and patterns the answer must or must not contain. They
  cost nothing, never flip between runs, and fail a case regardless of the judge.
* **Variables**: ``${table}`` and friends, defined once per suite and overridable
  on the command line, so the library follows the data in whichever environment
  it runs against instead of hardcoding one customer's table names.

File shape::

    suite: business-user
    description: ...
    vars: {table: catalog.schema.table}
    defaults:
      role: User
      answer_not_matches: ["(?i)\\\\bnoop\\\\b"]   # applied to every case
    cases:
      - id: bu-owner
        name: Table owner
        turns: ["Who owns ${table}?"]            # or  question: "..."
        expected: >
          Plain-English description of the behavior.
        expect_tools: [find_owner, [search_data_assets, get_table_list]]
        forbid_tools: [execute_workflow]
        tool_limits: {submit_feedback: 1}
        expect_tool_args:                       # what a call passed, not just that it was made
          - {tool: ask_your_data, arg: conversation_id, turn: 2, matches: "\\\\S"}
          - {tool: ask_your_data, arg: question, not_matches: "(?i)\\\\bselect\\\\b"}
        max_tool_calls: 6                       # whole conversation; suite default under `defaults`
        answer_matches: ["(?i)owner"]
        answer_not_matches: ["(?i)token"]
        needs_identity: false                   # true: needs the caller's own Databricks identity
        requires_workflows: [some_workflow]     # skipped (not failed) unless these are published
        requires_tools: [ask_your_data]         # skipped (not failed) unless the role is given these tools
        enabled: true
"""
from __future__ import annotations

import logging
import re
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

import yaml

logger = logging.getLogger(__name__)

CASES_DIR = Path(__file__).parent / "cases"

# A tool requirement is a name, or a list of names of which any one satisfies it.
ToolRequirement = Union[str, List[str]]


class LibraryError(ValueError):
    """The case files are malformed. Raised with the file and case, not a stack."""


@dataclass
class ToolArgExpectation:
    """What a call to ``tool`` must pass for ``arg``.

    Calling the right tool is not always enough: a Genie follow-up that omits the
    conversation id starts over, and a question rewritten as SQL defeats the point of
    asking Genie. Every matching call (those on ``turn`` when it is set) must satisfy
    the patterns, and at least one call must exist.
    """

    tool: str
    arg: str
    matches: Optional[str] = None
    not_matches: Optional[str] = None
    turn: Optional[int] = None


@dataclass
class EvalCase:
    id: str
    suite: str
    name: str
    turns: List[str]
    expected: str
    tags: List[str] = field(default_factory=list)
    role: str = "User"
    expect_tools: List[ToolRequirement] = field(default_factory=list)
    forbid_tools: List[str] = field(default_factory=list)
    tool_limits: Dict[str, int] = field(default_factory=dict)
    expect_tool_args: List[ToolArgExpectation] = field(default_factory=list)
    # Cap on tool calls across all turns. Catches an assistant that loops through
    # lookups it doesn't need, which reads fine in the answer but costs a minute
    # and a lot of tokens. ``None`` means no cap.
    max_tool_calls: Optional[int] = None
    answer_matches: List[str] = field(default_factory=list)
    answer_not_matches: List[str] = field(default_factory=list)
    needs_identity: bool = False
    # Workflows that must be published for the case to mean anything. Without them the
    # case is skipped, so a missing prerequisite is never scored as an assistant failure.
    requires_workflows: List[str] = field(default_factory=list)
    # Tools the case is about that an admin can switch off (Genie, say). When the role is
    # not given them the case is skipped: an assistant that cannot call a tool has not
    # failed to use it.
    requires_tools: List[str] = field(default_factory=list)
    enabled: bool = True
    notes: str = ""


@dataclass
class Suite:
    name: str
    description: str
    vars: Dict[str, str]
    cases: List[EvalCase]
    source: str = ""


def _as_list(value: Any, *, where: str) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    raise LibraryError(f"{where}: expected a list, got {type(value).__name__}")


def _render(text: str, variables: Dict[str, str], *, where: str) -> str:
    """Substitute ``${var}``; an unknown variable is an error, never silent text.

    ``safe_substitute`` would leave ``${typo}`` in the question and the agent would
    be graded on a sentence nobody wrote.
    """
    try:
        return string.Template(text).substitute(variables)
    except KeyError as e:
        raise LibraryError(f"{where}: unknown variable ${{{e.args[0]}}}") from None


def _optional_int(value: Any, *, where: str) -> Optional[int]:
    if value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise LibraryError(f"{where}: max_tool_calls must be a whole number") from None
    if number < 1:
        raise LibraryError(f"{where}: max_tool_calls must be at least 1")
    return number


def _compile(patterns: Sequence[str], *, where: str) -> None:
    for pattern in patterns:
        try:
            re.compile(pattern)
        except re.error as e:
            raise LibraryError(f"{where}: bad pattern {pattern!r}: {e}") from None


def _parse_tool_args(
    raw: Any, variables: Dict[str, str], *, where: str
) -> List[ToolArgExpectation]:
    parsed: List[ToolArgExpectation] = []
    for entry in _as_list(raw, where=where):
        if not isinstance(entry, dict) or not entry.get("tool") or not entry.get("arg"):
            raise LibraryError(f"{where}: each expect_tool_args entry needs 'tool' and 'arg'")
        matches = entry.get("matches")
        not_matches = entry.get("not_matches")
        if matches is None and not_matches is None:
            raise LibraryError(
                f"{where}: expect_tool_args for {entry['tool']}.{entry['arg']} needs 'matches' or 'not_matches'"
            )
        turn = entry.get("turn")
        if turn is not None:
            try:
                turn = int(turn)
            except (TypeError, ValueError):
                raise LibraryError(f"{where}: expect_tool_args turn must be a whole number") from None
            if turn < 1:
                raise LibraryError(f"{where}: expect_tool_args turn counts from 1")
        rendered = ToolArgExpectation(
            tool=str(entry["tool"]),
            arg=str(entry["arg"]),
            matches=_render(str(matches), variables, where=where) if matches is not None else None,
            not_matches=str(not_matches) if not_matches is not None else None,
            turn=turn,
        )
        _compile([p for p in (rendered.matches, rendered.not_matches) if p is not None], where=where)
        parsed.append(rendered)
    return parsed


def parse_suite(
    raw: Dict[str, Any],
    *,
    source: str = "<memory>",
    overrides: Optional[Dict[str, str]] = None,
) -> Suite:
    if not isinstance(raw, dict) or not raw.get("suite"):
        raise LibraryError(f"{source}: missing top-level 'suite' name")
    suite_name = str(raw["suite"])
    variables = {str(k): str(v) for k, v in (raw.get("vars") or {}).items()}
    variables.update({str(k): str(v) for k, v in (overrides or {}).items()})
    defaults = raw.get("defaults") or {}
    default_role = str(defaults.get("role") or "User")
    default_not = [str(p) for p in _as_list(defaults.get("answer_not_matches"), where=f"{source} defaults")]
    _compile(default_not, where=f"{source} defaults")

    cases: List[EvalCase] = []
    seen: set = set()
    for index, item in enumerate(_as_list(raw.get("cases"), where=f"{source} cases")):
        if not isinstance(item, dict) or not item.get("id"):
            raise LibraryError(f"{source}: case #{index + 1} has no id")
        case_id = str(item["id"])
        where = f"{source} case {case_id}"
        if case_id in seen:
            raise LibraryError(f"{where}: duplicate id")
        seen.add(case_id)

        turns_raw = item.get("turns")
        if turns_raw is None and item.get("question"):
            turns_raw = [item["question"]]
        turns = [_render(str(t).strip(), variables, where=where) for t in _as_list(turns_raw, where=where)]
        if not turns or not all(turns):
            raise LibraryError(f"{where}: needs 'question' or a non-empty 'turns' list")
        expected = _render(str(item.get("expected") or "").strip(), variables, where=where)
        if not expected:
            # A case with nothing to compare against can only ever "pass".
            raise LibraryError(f"{where}: needs an 'expected' outcome")

        expect_tools: List[ToolRequirement] = []
        for req in _as_list(item.get("expect_tools"), where=where):
            expect_tools.append([str(x) for x in req] if isinstance(req, (list, tuple)) else str(req))
        matches = [_render(str(p), variables, where=where) for p in _as_list(item.get("answer_matches"), where=where)]
        not_matches = [str(p) for p in _as_list(item.get("answer_not_matches"), where=where)]
        _compile(matches + not_matches, where=where)
        limits_raw = item.get("tool_limits") or {}
        if not isinstance(limits_raw, dict):
            raise LibraryError(f"{where}: tool_limits must be a mapping of tool name to a maximum")

        cases.append(
            EvalCase(
                id=case_id,
                suite=suite_name,
                name=str(item.get("name") or case_id),
                turns=turns,
                expected=expected,
                tags=[str(t) for t in _as_list(item.get("tags"), where=where)],
                role=str(item.get("role") or default_role),
                expect_tools=expect_tools,
                forbid_tools=[str(t) for t in _as_list(item.get("forbid_tools"), where=where)],
                tool_limits={str(k): int(v) for k, v in limits_raw.items()},
                expect_tool_args=_parse_tool_args(item.get("expect_tool_args"), variables, where=where),
                max_tool_calls=_optional_int(
                    item.get("max_tool_calls", defaults.get("max_tool_calls")), where=where
                ),
                answer_matches=matches,
                answer_not_matches=default_not + not_matches,
                needs_identity=bool(item.get("needs_identity", False)),
                requires_workflows=[str(w) for w in _as_list(item.get("requires_workflows"), where=where)],
                requires_tools=[str(t) for t in _as_list(item.get("requires_tools"), where=where)],
                enabled=bool(item.get("enabled", True)),
                notes=str(item.get("notes") or ""),
            )
        )
    return Suite(
        name=suite_name,
        description=str(raw.get("description") or "").strip(),
        vars=variables,
        cases=cases,
        source=source,
    )


def load_suites(
    directory: Optional[Path] = None,
    *,
    overrides: Optional[Dict[str, str]] = None,
) -> List[Suite]:
    """Load every ``*.yaml`` suite in ``directory`` (default: the shipped library)."""
    root = Path(directory) if directory else CASES_DIR
    suites: List[Suite] = []
    for path in sorted(root.glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            raise LibraryError(f"{path.name}: not valid YAML: {e}") from None
        suites.append(parse_suite(raw, source=path.name, overrides=overrides))
    names = [s.name for s in suites]
    if len(names) != len(set(names)):
        raise LibraryError("two files define the same suite name")
    return suites


def select_cases(
    suites: Sequence[Suite],
    *,
    suite: Optional[str] = None,
    case_ids: Optional[Sequence[str]] = None,
    tags: Optional[Sequence[str]] = None,
    include_disabled: bool = False,
) -> List[EvalCase]:
    """Filter the library. Unknown ids are an error so a typo doesn't run nothing."""
    wanted_ids = list(case_ids or [])
    out: List[EvalCase] = []
    for s in suites:
        if suite and s.name != suite:
            continue
        for case in s.cases:
            if wanted_ids and case.id not in wanted_ids:
                continue
            if tags and not set(tags) & set(case.tags):
                continue
            if not case.enabled and not include_disabled:
                continue
            out.append(case)
    missing = [i for i in wanted_ids if i not in {c.id for c in out}]
    if missing:
        raise LibraryError(f"no such case: {', '.join(missing)}")
    return out
