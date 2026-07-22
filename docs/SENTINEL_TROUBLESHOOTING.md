# Enforcement Sentinel — Troubleshooting & Regression Log (2026‑07‑21)

Working record of the day's Enforcement Sentinel debugging: the symptom, every
change we made, what we tried, and the root causes we found and fixed.

> ✅ **RESOLVED (2026‑07‑21 ~15:40 PDT).** A full run **completed end‑to‑end** on
> the air‑gapped env with detection restored: **45,833 checks · 19,404 pass ·
> 26,429 violations (2,532 HIGH / 11,949 MEDIUM) · 14,481 violations listed** for
> `ws-enterprise-prod`. This confirms the two fixes below (Finding 1 + Finding 3).
> A revert to the known‑good baseline was **not** needed — the day's payload/perf
> work is retained and the detached execution model was fixed in place rather than
> reverted. **Not yet committed.** Remaining follow‑up is **speed only** (Section 8),
> not correctness.

> ⚠️ **Environment caveat (read first).** The real system is an **air‑gapped
> deployed Databricks App** backed by **Lakebase/Postgres**. Nothing checked
> locally reflects it. The local SQLite DB (`backend/app_hub.db`), local
> `./dev.sh` runs, and any query/test run outside this codebase are the **wrong
> environment** and must not be used to conclude "it works." Reproduction and
> verification only count on the deployed air‑gapped env. Code reasoning and git
> history are trustworthy; local runtime state is not.

---

## 1. Symptom (as reported)

- A Sentinel run goes to **`discovering`** and **stays there 15+ minutes**.
- **No logs of any kind** appear while it sits there — no timing logs, no
  progress logs, no errors.
- Logs only appear **when the user interacts with the site** (e.g. changes a
  page). The Sentinel *just running* produces **nothing**.
- Earlier in the day the behavior was "slow" (250+ MB payloads, long loads) but
  it **completed**. After the day's changes it "**only fails**," at one point
  detecting `11 found` (a tiny fraction of the real violations), and at other
  points hanging silently as above.

The shift from **slow‑but‑working** → **silent‑and‑stuck** is the key signal:
something we changed today stops the run from making (or reporting) progress.

---

## 2. Known‑good baseline & today's commits

- **Last known‑good commit:** `11e7ce8` — *"Add Prune Option to Workflow Import
  Functionality"* (2026‑07‑20 17:29). This is the last commit **before** today's
  Sentinel work.
- **Current HEAD:** `36703e2` (2026‑07‑21 15:51).
- Branch: `feature/oops-all-agents`.

All 13 of today's commits sit on top of `11e7ce8`:

| # | Commit | Time | Title | Area |
|---|--------|------|-------|------|
| 1 | `78daa83` | 09:10 | Request API ORJSONResponse + metadata summarization | mixed (perf + unrelated FE) |
| 2 | `3749668` | 09:24 | Workflow Tombstone Management + deletion logic | **workflow (keep)** |
| 3 | `a3818b7` | 10:09 | Refactor Sentinel for state mgmt + performance | mixed (sentinel + workflow) |
| 4 | `34b70e6` | 10:24 | Purge functionality for Sentinel runs | sentinel util |
| 5 | `19be2d8` | 11:23 | Notebook handler NoneType fix | **bugfix (keep)** |
| 6 | `3603758` | 12:35 | Sentinel run mgmt + logging; log‑noise ↓; LARGE compute | mixed (keep bits) |
| 7 | `8cdbe78` | 12:49 | Purge force option | sentinel util |
| 8 | `4d59a84` | 13:16 | **Per‑workspace scan timeout + detached request processing** | **sentinel‑risk** |
| 9 | `89eb155` | 13:43 | Databricks SDK timeout config + client init | sentinel‑risk |
| 10 | `7a7adb2` | 14:15 | **Refactor request processing (detached) + logging** | **sentinel‑risk** |
| 11 | `4f71875` | 15:01 | **Workspace scan timeout → 0 (disabled)** | **sentinel‑risk** |
| 12 | `17d2f9e` | 15:38 | Timeout docs + session handling | sentinel‑risk |
| 13 | `36703e2` | 15:51 | Workspace concurrency + notebook‑scan toggle | sentinel perf |

Diffstat `11e7ce8..HEAD`: **34 files, +2024 / −247**. The heavy hitters are
`backend/app/workflows/sentinel.py` (+434), `backend/app/workers/poller.py`
(+256), `src/pages/admin/EnforcementSentinel.tsx` (+235),
`backend/app/services/sentinel_findings.py` (+239), `backend/app/api/v1/requests.py`
(+148), `backend/app/api/v1/governance.py` (+130), `backend/app/core/config.py` (+100).

---

## 3. What we changed today, grouped by intent

### A. Payload / memory / performance (the work worth re‑applying)
The original problem: the run persisted **everything** (all violations + checks)
into `requests.state_context`, producing **250+ MB** payloads that blew up the
list page, the emailed report, and eventually caused **OOM** crashes.

- **New `sentinel_findings` table** (`backend/app/db/sentinel_finding.py`,
  `backend/app/services/sentinel_findings.py`): individual violations/checks moved
  out of the `state_context` JSON blob into their own rows; `replace_run_findings`
  streams batched inserts to avoid a huge single write (was dropping the SSL
  connection at ~26k rows).
- **`state_summary`** compact column + `backend/app/services/state_summary.py`
  and a startup backfill (`backend/app/db/migrate.py`): list view reads a small
  summary, not the full context. `state_context` is deferred on list queries.
- **`enforcement_sentinel.json`** graph + `tools.py` `sentinel_discover`: now
  return only summary counts, not the full findings arrays.
- **`ORJSONResponse` → custom `_orjson_response`** in `requests.py`
  (also silences the FastAPI deprecation warning).
- **`databricks.yml`**: prod app compute bumped `MEDIUM → LARGE`.
- Frontend `EnforcementSentinel.tsx`: removed truncation notice, added an
  in‑flight **polling guard** (stop stacking 15s+ requests), and an admin
  **"Clear old runs"** button.

**Verdict:** this is the "make it fast/lean" work the user wants to keep — but
only *after* the run reliably completes again.

### B. Concurrency / durability / timeouts (the risky work)
Added to stop hangs and connection churn — but this is where regressions live.

- **Detached poller processing** (`poller.py`, commits `4d59a84`, `7a7adb2`,
  `17d2f9e`): `ENFORCEMENT_SENTINEL` is no longer awaited in the batch; it's
  fired off with `asyncio.create_task` so a slow scan can't freeze the poll loop.
- **Per‑workspace scan timeout** `SENTINEL_WORKSPACE_SCAN_TIMEOUT_SECONDS`
  wrapping each workspace in `asyncio.wait_for` — **then defaulted to `0`
  (disabled)** in `4f71875` to stop it abandoning large workspaces (the `11 found`
  under‑detection).
- **Global SDK timeouts** (`89eb155`): monkeypatch on `databricks.sdk` `Config`
  (`DATABRICKS_HTTP_TIMEOUT_SECONDS`, `DATABRICKS_RETRY_TIMEOUT_SECONDS`) plus a
  longer Sentinel‑specific `SENTINEL_SDK_HTTP_TIMEOUT_SECONDS`.
- **Dedicated Sentinel thread pool** `_sentinel_executor` / `_to_sentinel_thread`.
- **Parallel workspace scans** `SENTINEL_WORKSPACE_CONCURRENCY` (default 3) and
  **notebook scan toggle** `SENTINEL_SCAN_NOTEBOOKS` (default off) — `36703e2`.
- **Fresh‑session persistence + retries** in `poller.py` / `sentinel.py`; removed
  the global `reset_database_connection()` engine‑pool dispose from per‑request
  recovery helpers (it was corrupting other in‑flight requests once Sentinel ran
  detached).

### C. Unrelated / keep regardless
- Workflow **tombstones + seed‑pruning** (`3749668`, workflow part of `a3818b7`,
  `workflow_service.py`): the import/prune fix — unrelated to Sentinel, **keep**.
- **Notebook handler NoneType** fix (`19be2d8`) — **keep**.
- **Log‑noise reduction** (`auth.py`, `deps.py` in `3603758`) — **keep**.
- Frontend `ErrorBoundary.tsx`, `AssistantShelf.tsx`, `Workflows.tsx`,
  `workflowSpec.ts`, `cluster_handler.py`, `prompts.py` (in `78daa83`) — **keep**.

---

## 4. What we tried (chronological)

1. **Moved findings out of `state_context`** into `sentinel_findings` + streamed
   batched inserts. Fixed the SSL drop on large writes and shrank list payloads.
2. **`state_summary` + deferred `state_context`.** Faster list, but old runs still
   slow until enough new lean runs exist → added the **"Clear old runs"** purge
   button (with a `force` option for stuck/non‑terminal runs).
3. **Notebook handler NoneType** crash fixed (partial scans).
4. **OOM crash after 10–15 min:** trimmed `state_context`/tool return/checkpoint,
   streamed inserts.
5. **Stuck `discovering` runs:** enhanced purge to force‑delete non‑terminal runs.
6. **Silent hang in discovery:** added `SENTINEL_WORKSPACE_SCAN_TIMEOUT_SECONDS`
   + `asyncio.wait_for` + granular INFO progress logs.
7. **"Everything is broken / nothing moves":** made the poller launch Sentinel
   **detached** so a hung scan can't freeze the whole loop.
8. **SSL closed / `ObjectDeletedError` / completed runs marked FAILED:**
   fresh‑session final persistence; removed global engine‑pool dispose from
   recovery helpers; added retry to the terminal‑status write.
9. **SDK calls hanging:** global + Sentinel‑specific SDK timeouts; dedicated
   thread pool.
10. **Under‑detection (`11 found`):** traced to the 600s per‑workspace timeout
    abandoning big workspaces → **disabled the timeout (default 0)**.
11. **Speed (Tier 1):** parallelize workspaces (`SENTINEL_WORKSPACE_CONCURRENCY`),
    remove notebook scope by default (`SENTINEL_SCAN_NOTEBOOKS=false`), per‑handler
    timing logs.

Despite all this, the run still sits in `discovering` with **no logs** — which
points away from "a specific handler is slow" and toward "the task isn't running
(or its failure is being swallowed)."

---

## 5. Suspected root cause — surfaced for a decision (NOT fixed)

Two changes from today combine to produce *exactly* "stuck in `discovering`, no
logs, no error." **These were not touched — flagging for your call.**

### 🔴 Finding 1 (primary): the detached Sentinel task is never referenced → it can be garbage‑collected mid‑run

`backend/app/workers/poller.py`:

```283:308:backend/app/workers/poller.py
def _launch_detached(request_id: str) -> None:
    ...
    if request_id in _inflight_detached:
        return
    _inflight_detached.add(request_id)

    async def _run() -> None:
        try:
            await process_single_request(
                asyncio.Semaphore(settings.POLLER_MAX_CONCURRENT), request_id
            )
        except Exception as e:  # noqa: BLE001
            logger.error(
                "Detached processing of request %s failed: %s", request_id, e,
                exc_info=True,
            )
        finally:
            _inflight_detached.discard(request_id)

    asyncio.create_task(_run())
```

The `Task` returned by `asyncio.create_task(_run())` is **never stored**.
`_inflight_detached` holds only the request‑id **string**, not the task object.
Per the CPython docs, *the event loop keeps only a **weak** reference to tasks; a
task not referenced elsewhere can be garbage‑collected at any time, even before
it finishes.* When that happens the coroutine is **silently cancelled**:

- the run keeps whatever status it last wrote (**`discovering`**),
- **no exception** reaches the `except` (so nothing is logged),
- the `finally` may not run either.

This matches the symptom precisely, and it explains **why logs only appear when
you touch the site**: request handlers run on the loop and log normally, but the
background Sentinel task has quietly died. It is also **more likely under memory
pressure** — and this app has been OOM‑prone today, which makes GC aggressive.

This behavior is **new today** (the detached model landed in `4d59a84` /
`7a7adb2`). Before today the Sentinel ran **inline in the awaited batch**: slow,
but referenced for its whole lifetime, so it couldn't vanish — consistent with
"it used to be slow but complete."

*Shape of the fix (for later, not applied):* keep a strong reference to the task
(e.g. store the `Task` itself in a module‑level set and discard it in a
`add_done_callback`), instead of tracking only the id string.

### 🟠 Finding 2 (contributing): the per‑workspace timeout is disabled, so a genuine hang has no backstop

`backend/app/workflows/sentinel.py`:

```1124:1128:backend/app/workflows/sentinel.py
    timeout = getattr(settings, "SENTINEL_WORKSPACE_SCAN_TIMEOUT_SECONDS", 0)
    if timeout and timeout > 0:
        return await asyncio.wait_for(_scan_and_evaluate(**kwargs), timeout=timeout)
    return await _scan_and_evaluate(**kwargs)
```

We set `SENTINEL_WORKSPACE_SCAN_TIMEOUT_SECONDS = 0` (commit `4f71875`) to stop
the cap from abandoning large workspaces. Side effect: if a workspace scan (SDK
auth probe, resource listing, or OPA eval) genuinely hangs, there is now **no
timeout to break it and no `_timeout_failure` log** — it stalls forever, silently.
On its own this doesn't kill the run, but layered on Finding 1 it removes the one
mechanism that would have produced a log line during a stall.

**Net:** Finding 1 is the likely reason for the total silence + permanent
`discovering`; Finding 2 removes the safety net that used to at least log a
timeout. Both are today‑only regressions.

---

## 5a. Applied fixes (live log — update as we go)

| Date/time | Change | Commit | Verified on air‑gapped env? |
|-----------|--------|--------|------------------------------|
| 2026‑07‑21 16:3x | **Finding 1 fix:** `poller.py` now keeps a **strong reference** to each detached Sentinel task (`_detached_tasks` set + `add_done_callback` to discard) so the loop can't GC it mid‑run. Task also named `sentinel-detached-<id>`. Timeout left at `0` on purpose (see below). | _pending commit_ | ✅ **Confirmed** — logs + per‑handler timing now flow, tasks survive (see next row for the duplicate this exposed) |
| 2026‑07‑21 16:4x | **Finding 3 fix (strict one‑at‑a‑time):** (1) cron now marks **all** orphaned/stale non‑terminal Sentinel runs **FAILED** (clears lock, sets `last_error`) before spawning a fresh one; (2) `process_open_requests` launches **at most one** Sentinel scan cluster‑wide — extra non‑terminal rows are **deferred** (logged), never run concurrently. User chose strict semantics (one Sentinel scan cluster‑wide at any time; manual and scheduled do not overlap). | _pending commit_ | ✅ **Confirmed** — subsequent run completed as a single scan (no duplicate) |

**Why Finding 1 only, first:** it's the primary suspect and the lowest‑risk,
highest‑signal change. We deliberately did **not** also re‑enable the timeout
(Finding 2) in the same step, so the experiment stays clean:

- If the run now **completes** → Finding 1 was the cause. 
- If it **still hangs** → the task now survives, so we should start seeing the
  normal progress logs (`"Sentinel: resolving target workspaces..."` →
  `"...authenticating..."` → per‑handler timing). Wherever the logs stop is the
  real hang location — then address Finding 2 / that specific call.

**Watch for on next run:** the first INFO line `"Sentinel: resolving target
workspaces and credentials..."` (`sentinel.py:1183`). If it appears (it didn't
before), the task is no longer dying before discovery.

---

## 5b. Finding 3 — duplicate concurrent scans ("why do I see 2?") — surfaced, NOT fixed

After the Finding 1 fix, logs flow and scans run — but **two** scans ran in
parallel with **different `req_id`s** (same workspace, same ~1003 resources, same
handler timings). Root cause is two compounding gaps:

1. **Cron spawns a fresh run but never terminates the stale one it ignores.**
   `process_enforcement_sentinel_cron` (`poller.py:93‑120`): when it finds a
   non‑terminal run that's *stale* (no recent `updated_at`, expired `locked_until`)
   it logs `"...appears stale...; spawning a fresh scheduled run"` and creates a
   new PENDING request — but leaves the stale row **non‑terminal**. All day, runs
   were getting orphaned (the Finding‑1 GC death), so each cron tick left the old
   orphan and added a new row.
2. **`process_open_requests` runs every non‑terminal Sentinel row concurrently.**
   The pickup query (`poller.py:335‑355`) returns *all* non‑terminal, unlocked
   requests up to `POLLER_BATCH_SIZE`, and the detached branch launches a task per
   row. There is **no "one Sentinel scan at a time" guard**. So the accumulated
   orphan + the fresh run both execute at once → doubled Databricks API load and
   peak memory (a real OOM risk).

Why it only showed up now: before the Finding‑1 fix those orphaned runs' tasks
died silently, so they never actually *ran*. Fixing Finding 1 made the latent
duplicate rows execute.

**Proposed fix (awaiting your call on semantics):**
- In the cron, when a stale run is detected, **mark it FAILED (terminal)** before
  spawning the fresh one, so it can't be re‑picked.
- Add a **single‑active‑Sentinel guard**: `process_open_requests` (and/or
  `_launch_detached`) launches at most one `ENFORCEMENT_SENTINEL` at a time;
  additional non‑terminal Sentinel rows are skipped (or failed as duplicates).
- Open question for the user: should a **manual** run be allowed to coexist with a
  scheduled one, or is it strictly one Sentinel scan cluster‑wide at any time?

Status: ✅ **implemented** (see Section 5a, second row). User chose **strict
one‑at‑a‑time**: (1) cron fails orphaned/stale runs before spawning; (2) the
poller launches at most one Sentinel scan cluster‑wide and defers the rest. A
healthy long run renews its lock via the heartbeat, so it's treated as active and
never failed.

**Clearing the already‑duplicated state on the deployed env:** the extra orphan
row(s) already present will be marked FAILED automatically by the next cron tick
that finds no active run (or once the current run finishes), or you can clear them
now with the admin **"Clear old runs"** (force) button.

---

## 5c. Finding 4 — `'home'` shows as a separate target (observation, awaiting decision)

The Sentinel runs a **Unity Catalog data‑certification pass** *once* per run,
separate from the per‑target workspace scans, because UC is **account‑global**
(shared metastore) — scanning data products per‑target would be redundant and can
fail cross‑workspace TLS. This pass runs only `DatasetResourceHandler`, pinned to
the home host, and is labeled `workspace='home'`. In dev it returns 0 (no data
products). **It does NOT re‑scan the home target's workspace‑scoped resources.**

It shows as `'home'` (not the user's target name) because the cert→target mapping
uses **exact host string equality**:

```1221:1222:backend/app/workflows/sentinel.py
    home_host = settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL
    home_match = next((w for w in all_ws if w.host == home_host), None)
```

`home_match` was `None`, so it fell back to the synthetic `'home'` + the app's own
SP (`global_default`). The user states the home workspace **is** a defined target,
so this is most likely a **host normalization mismatch** (scheme / trailing slash /
case) rather than a genuinely extra workspace.

**Options (pending user decision):**
- **B — normalize host matching (code):** compare hosts after stripping
  scheme/trailing slash/case so a defined target equal to the home host is
  recognized, labeled by its target name, and uses its SP. Robust, low‑risk.
- **A — no‑code:** set `SENTINEL_DATA_CERT_WORKSPACE` (Admin → Settings) to the
  home target's name so the cert pass explicitly runs under that defined target.
- **C — gate the dataset/cert pass** behind a setting (like `SENTINEL_SCAN_NOTEBOOKS`)
  for envs with no data products to govern.

Status: ✅ **Resolved — working as intended, no change.** Confirmed with the user:
the data‑certification pass should always run against the local/home workspace
(UC is account‑global). The `'home'` label is expected; it is not an extra target
and does not re‑scan the home target's workspace‑scoped resources.

---

## 6. Path actually taken (updated — no revert needed)

The original plan was a full revert to `11e7ce8` + careful re‑apply. That turned
out to be unnecessary: once Finding 1 was identified, fixing the detached model
**in place** restored a working run without giving up the day's payload/perf work.

1. ~~Revert Sentinel runtime to `11e7ce8` (inline/awaited).~~ **Not done** — the
   detached model was fixed in place (Finding 1) instead. Kept detached so a slow
   scan can't freeze the poll loop.
2. **Retained** the non‑Sentinel work (Section 3C) and the schema/payload
   improvements (Section 3A: `sentinel_findings`, `state_summary`, trimmed
   `state_context`, ORJSON, LARGE compute, polling guard, purge button).
3. **Kept** the concurrency/durability work (Section 3B) with the Finding 1 +
   Finding 3 fixes layered on top.
4. **Finding 2 (backstop timeout) still open by choice:** left at `0` (disabled)
   so it can't abandon large workspaces. Revisit only if a real hang recurs; a
   re‑introduced value must be generous enough not to abandon the biggest
   workspace (`enterprise-us-dev`‑class) yet still break a true stall.
5. **Verify only on the deployed air‑gapped env.** First expected INFO line:
   `"Sentinel: resolving target workspaces and credentials..."` (`sentinel.py:1183`).

---

## 7. Execution model & speed (answering "did we reintroduce blocking?")

**No — the app‑wide freeze was NOT reintroduced.** The Sentinel still runs
**detached**, and every blocking piece is offloaded off the event loop:

- **Detached launch** — `_launch_detached` fires the run as its own task (now with
  a strong reference, Finding 1). It is *not* `await`ed inside the poll loop, so
  the poll loop keeps cycling and other requests process normally. The old
  "nothing moves" freeze came specifically from `await asyncio.gather(<sentinel>)`
  in `process_open_requests`; that is gone and was not restored.
- **Discovery** (Databricks SDK calls) runs on the dedicated `_sentinel_executor`
  thread pool via `_to_sentinel_thread` — off the event loop.
- **OPA evaluation** never blocks the loop: remote mode uses async `httpx`
  (`_evaluate_remote`); local CLI mode offloads `subprocess.run` via
  `asyncio.to_thread` (`opa/client.py:315`).

Net: the event loop stays responsive during a scan. "Moving it to the main thread"
did **not** happen.

### Speed status
- **Correctness: solved.** Full detection restored (45,833 checks on the
  completing run).
- **Tier 1 speed work already in:** parallel workspace scans
  (`SENTINEL_WORKSPACE_CONCURRENCY`, default 3), notebook discovery **off** by
  default (`SENTINEL_SCAN_NOTEBOOKS=false` — was the single most expensive step),
  in‑workspace concurrency (`SENTINEL_SCAN_CONCURRENCY`), and **per‑handler timing
  logs** now emitted (e.g. `ClusterResourceHandler=100 in 2.6s`).
- **Next (Tier 2), evidence‑driven:** read the per‑handler timing lines from the
  completed run to find the dominant cost (handler discovery vs. OPA eval of ~46k
  checks vs. findings persistence), then target that specifically. Candidates:
  raise `SENTINEL_WORKSPACE_CONCURRENCY` / `SENTINEL_SCAN_CONCURRENCY`, batch/tune
  OPA evaluation, or cache discovery. **No speed change made yet** — pending the
  timing evidence from the deployed env (local numbers are not representative; see
  the environment caveat).

---

## 8. Quick reference — files most in play

- `backend/app/workers/poller.py` — detached processing, locks, recovery helpers.
- `backend/app/workflows/sentinel.py` — discovery, per‑workspace scan, timeouts,
  thread pool, logging.
- `backend/app/core/config.py` / `settings_store.py` — timeouts, concurrency,
  notebook toggle, SDK timeout monkeypatch.
- `backend/app/services/sentinel_findings.py` + `backend/app/db/sentinel_finding.py`
  — findings table (payload fix).
- `backend/app/services/state_summary.py` + `backend/app/db/migrate.py` — summary
  column + backfill.
- `backend/app/providers/databricks/client.py` — SDK client timeout wiring.
- `src/pages/admin/EnforcementSentinel.tsx` — polling guard, purge button.
