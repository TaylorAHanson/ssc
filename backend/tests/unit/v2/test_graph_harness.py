"""Drives the V2 pre-publish eval/sandbox harness as part of the test suite.

The harness compiles every registered LangGraph workflow, runs it hermetically
(fake providers, no-op facts), proves each gate interrupts for HITL and resumes
to ``completed``, and asserts every mutation routed through the shared
ToolExecutor. We run it as a subprocess because the harness monkeypatches module
globals process-wide; isolating it keeps those patches out of the rest of the
suite.
"""
import os
import re
import subprocess
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))


def test_all_v2_graphs_green():
    proc = subprocess.run(
        [sys.executable, "-m", "app.v2.harness"],
        cwd=_BACKEND_DIR,
        capture_output=True,
        text=True,
    )
    output = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"V2 harness failed:\n{output}"

    # Last summary line: "<passed>/<total> graphs green | ToolExecutor calls=N (mutating=M)"
    m = re.search(r"(\d+)/(\d+) graphs green \| ToolExecutor calls=(\d+) \(mutating=(\d+)\)", output)
    assert m, f"could not find harness summary in output:\n{output}"
    passed, total, calls, mutating = (int(g) for g in m.groups())

    assert total >= 1, "no graphs registered"
    assert passed == total, f"only {passed}/{total} V2 graphs green:\n{output}"
    # Mutations must flow through the governed executor, never raw providers.
    assert mutating >= 1, "expected at least one mutating ToolExecutor call"
    assert calls >= mutating
