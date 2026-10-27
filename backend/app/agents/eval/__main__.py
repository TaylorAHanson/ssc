"""Command line for the assistant test library.

    cd backend && source venv/bin/activate
    python -m app.agents.eval list
    python -m app.agents.eval run --label baseline
    python -m app.agents.eval run --case bu-owner --case bu-table-details --no-judge
    python -m app.agents.eval run --obo-profile myenv --label with-identity
    python -m app.agents.eval compare baseline after
    python -m app.agents.eval show eval_reports/<file>.json

``run`` talks to the same model the running app uses (admin overrides from the
database are applied first) and writes a JSON report under ``eval_reports/``.
Nothing it runs changes anything: mutating tools are simulated.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.agents.eval import library, runner

REPORT_DIR = Path(__file__).resolve().parents[3] / "eval_reports"

logger = logging.getLogger("app.agents.eval")


def _out(text: str = "") -> None:
    # A CLI's output is its interface; the logging library is for diagnostics.
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def _apply_runtime_settings() -> None:
    """Load the admin-editable overrides so the run sees the same model as the app."""
    from app.core import settings_store
    from app.db.session import get_session_local

    db = get_session_local()()
    try:
        settings_store.load_overrides(db)
        # The same idempotent step app startup runs, so a tool added since the app
        # last started is in the registry and the run sees what the app would.
        from app.services.tool_registry_service import ToolRegistryService

        ToolRegistryService.sync_local_tools(db)
    finally:
        db.close()


def _cli_json(*args: str) -> Dict[str, Any]:
    proc = subprocess.run(["databricks", *args], capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        # Never echo stdout: for `auth token` it is the credential.
        raise SystemExit(f"`databricks {' '.join(args[:2])}` failed: {proc.stderr.strip()[:200]}")
    return json.loads(proc.stdout)


def _identity_from_profile(profile: str, email: Optional[str]) -> tuple[str, str]:
    token = _cli_json("auth", "token", "-p", profile, "-o", "json").get("access_token")
    if not token:
        raise SystemExit(f"No access token for profile {profile!r}; run `databricks auth login -p {profile}`.")
    if not email:
        email = _cli_json("current-user", "me", "-p", profile, "-o", "json").get("userName")
    if not email:
        raise SystemExit("Could not work out the profile's email; pass --email.")
    return token, email


def _parse_vars(pairs: List[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise SystemExit(f"--var expects name=value, got {pair!r}")
        key, value = pair.split("=", 1)
        out[key.strip()] = value
    return out


def _find_report(ref: str) -> Path:
    path = Path(ref)
    if path.is_file():
        return path
    matches = sorted(REPORT_DIR.glob(f"*{ref}*.json"), key=lambda p: p.stat().st_mtime)
    if not matches:
        raise SystemExit(f"No report matches {ref!r} in {REPORT_DIR}")
    return matches[-1]


def _load_report(ref: str) -> Dict[str, Any]:
    return json.loads(_find_report(ref).read_text(encoding="utf-8"))


# ------------------------------------------------------------------- commands
def cmd_list(args: argparse.Namespace) -> int:
    suites = library.load_suites(overrides=_parse_vars(args.var))
    cases = library.select_cases(
        suites, suite=args.suite, tags=args.tag, include_disabled=True
    )
    for suite in suites:
        if args.suite and suite.name != args.suite:
            continue
        _out(f"{suite.name}: {suite.description}")
        for case in (c for c in cases if c.suite == suite.name):
            flags = []
            if case.needs_identity:
                flags.append("needs identity")
            if case.requires_workflows:
                flags.append("needs workflow: " + ", ".join(case.requires_workflows))
            if case.requires_tools:
                flags.append("needs tool: " + ", ".join(case.requires_tools))
            if not case.enabled:
                flags.append("disabled")
            tags = f" [{', '.join(case.tags)}]" if case.tags else ""
            note = f"  ({'; '.join(flags)})" if flags else ""
            _out(f"  {case.id:<28} {case.name}{tags}{note}")
    _out(f"\n{len(cases)} case(s)")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    _apply_runtime_settings()

    suites = library.load_suites(overrides=_parse_vars(args.var))
    cases = library.select_cases(
        suites,
        suite=args.suite,
        case_ids=args.case,
        tags=args.tag,
        include_disabled=bool(args.case),
    )
    if not cases:
        raise SystemExit("No cases selected.")

    token: Optional[str] = None
    email = args.email or runner.DEFAULT_EMAIL
    if args.obo_profile:
        token, email = _identity_from_profile(args.obo_profile, args.email)
        _out(f"Running identity-dependent cases as {email} (profile {args.obo_profile}).")

    role_tools = {c.role: runner.resolve_tools(c.role, email) for c in cases}
    footprint = runner.prompt_footprint(next(iter(role_tools.values())), email=email)
    _out(
        f"{len(cases)} case(s) x {args.repeat}, judge {'off' if args.no_judge else 'on'}, "
        f"standing context ~{footprint.get('standing_tokens')} tokens "
        f"({footprint.get('tool_count')} tools)."
    )

    results = asyncio.run(
        runner.run_cases(
            cases,
            email=email,
            obo_token=token,
            use_judge=not args.no_judge,
            concurrency=args.concurrency,
            timeout=args.timeout,
            repeat=args.repeat,
            on_result=lambda r, done, total: _out(runner.format_result_line(r, done, total)),
            tools_for_role=lambda role: role_tools[role],
        )
    )

    report = runner.build_report(
        results,
        label=args.label or "",
        footprint=footprint,
        options={
            "suite": args.suite,
            "judge": not args.no_judge,
            "identity": bool(token),
            "concurrency": args.concurrency,
            "repeat": args.repeat,
            "vars": _parse_vars(args.var),
        },
    )
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"{args.suite or 'all'}-{stamp}" + (f"-{args.label}" if args.label else "") + ".json"
    path = REPORT_DIR / name
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    _out("")
    _out(runner.format_report(report))
    _out(f"\nReport: {path}")
    summary = report["summary"]
    return 1 if (summary["fail"] or summary["error"]) else 0


def cmd_show(args: argparse.Namespace) -> int:
    report = _load_report(args.report)
    _out(runner.format_report(report))
    if args.case:
        for r in report["results"]:
            if r["id"] in args.case:
                _out("")
                _out(f"--- {r['id']} ({r['status']}) ---")
                _out(f"Turns: {r['turns']}")
                for call in r.get("tool_calls") or []:
                    _out(f"  tool: {call['name']}({json.dumps(call.get('arguments'), default=str)[:200]})")
                _out(f"Answer:\n{r.get('answer')}")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    _out(runner.compare_reports(_load_report(args.a), _load_report(args.b)))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.agents.eval", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def _selection(p: argparse.ArgumentParser) -> None:
        p.add_argument("--suite", help="Only this suite")
        p.add_argument("--tag", action="append", default=[], help="Only cases with this tag (repeatable)")
        p.add_argument("--var", action="append", default=[], metavar="NAME=VALUE", help="Override a suite variable")

    p_list = sub.add_parser("list", help="Show the cases in the library")
    _selection(p_list)
    p_list.set_defaults(func=cmd_list)

    p_run = sub.add_parser("run", help="Run cases against the real agent and judge them")
    _selection(p_run)
    p_run.add_argument("--case", action="append", default=[], help="Run only this case id (repeatable)")
    p_run.add_argument("--label", help="Name for this run, used in the report file and by `compare`")
    p_run.add_argument("--no-judge", action="store_true", help="Deterministic checks only; no judge call")
    p_run.add_argument("--concurrency", type=int, default=runner.DEFAULT_CONCURRENCY, help="Cases at once; each turn sends the whole prompt, so more trips the model rate limit")
    p_run.add_argument(
        "--repeat", type=int, default=1,
        help="Run every case N times; the model varies between runs, so compare repeated runs with repeated runs",
    )
    p_run.add_argument("--timeout", type=int, default=runner.DEFAULT_TIMEOUT_SECONDS, help="Seconds per case")
    p_run.add_argument("--obo-profile", help="Databricks CLI profile; runs identity-dependent cases as that user")
    p_run.add_argument("--email", help="Email for the identity (default: looked up from the profile)")
    p_run.set_defaults(func=cmd_run)

    p_show = sub.add_parser("show", help="Print a saved report")
    p_show.add_argument("report", help="Path, or part of a file name or label")
    p_show.add_argument("--case", action="append", default=[], help="Also print this case's tools and answer")
    p_show.set_defaults(func=cmd_show)

    p_cmp = sub.add_parser("compare", help="Compare two saved reports (A = before, B = after)")
    p_cmp.add_argument("a", help="Path, or part of a file name or label")
    p_cmp.add_argument("b", help="Path, or part of a file name or label")
    p_cmp.set_defaults(func=cmd_compare)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except library.LibraryError as e:
        _out(f"Library error: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
