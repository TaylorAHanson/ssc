# Manual Test Script

A hands-on walkthrough to exercise the whole app and confirm the recent changes.
Work top to bottom; each step lists the **action** and the **expected result**.

> **Persona switching:** open the avatar menu (bottom-left) → use the dev role
> switcher to become **Platform Admin / Governance Admin / Security Admin /
> Finance Admin / User**. Steps below note which persona is needed.
>
> **Environment notes:** some features only light up in a deployed Databricks
> Apps environment where the user's OBO token is present (Genie, "Accessible to
> me", OBO-scoped governance/finops queries). In plain local dev those degrade
> gracefully — the script calls out where to expect that.

---

## 0. Pre-flight

- [x] Start the app: `./dev.sh` (add `--debug` to attach the debugger).
- [x] Backend health: open `http://localhost:8000/docs` → Swagger loads.
- [x] `tail -f backend.log` in a second terminal → no tracebacks on boot.
- [x] Frontend: open `http://localhost:5173` → app loads, brand name/logo/colors
      come from `configuration.yaml` (not hardcoded), no console errors.
- [x] **Startup migration:** in `backend.log`, confirm no migration errors. The
      `context_documents` table now has `retrieval_count` + `last_retrieved_at`
      columns (added idempotently on boot).

---

## 1. Global shell & navigation

- [x] Sidebar renders; collapse/expand it.
- [x] As **User**, admin/governance nav items are hidden.
- [x] Switch to **Platform Admin** → admin + governance + workflows items appear.
- [x] Visit each top route and confirm it loads without errors:
      `/` (Home), `/discovery`, `/requests`, `/approvals`, `/reports`,
      `/community/training`, `/community/events`, `/community/assets`,
      `/community/links`, `/command-center`.

---

## 2. Avatar menu  *(changed this session)*

- [x] Open the avatar menu. Confirm there is **no "Sign out" button** (removed —
      auth is workspace-owned via Databricks Apps).
- [x] **Send feedback** opens the feedback modal; submit a test note → success.
- [x] **Clear my data** (new): click it → confirmation dialog appears. Confirm →
      the page reloads, chat history is gone, and home suggestions are refetched.
      Cancel on a second attempt → nothing is cleared.

---

## 3. Core agent chat (Home)

- [x] On `/`, the home page shows personalized starting suggestions (or the
      static welcome examples if suggestions are disabled/unavailable).
- [x] Ask: *"What can you help me with?"* → agent streams a response; tool pills
      (if any) render and resolve.
- [x] Ask something that triggers a tool, e.g. *"List the catalogs I can see"* →
      a tool pill appears, runs, and the answer streams. No duplicate text in the
      reply (intra-message de-dup).
- [x] Start a **New chat** → history clears for the new turn.
- [x] **recent_topics redaction (optional/advanced):** ask a question containing a
      fake email + token, e.g. *"email jane@corp.example about token
      dapi0123456789abcdef0123"*. Reload Home so suggestions refetch. The raw
      email/token must **not** reach the model — verify via `backend.log` (the
      suggestions request) or trust the unit test
      `tests/unit/.../prompts` redaction. (This is a backend safeguard, not a
      visible UI change.)

---

## 4. Ask Your Data (Genie)  *(changed this session)*

> Requires Genie + OBO (deployed env). In local dev it may fall back to the SP
> or be unavailable — note the behavior rather than failing the run.

- [ ] Ask a data question (e.g. *"How many rows are in <some table>?"*). The
      "Asking Genie…" pill appears and streams progress.
- [ ] On completion, **Genie's answer renders verbatim** (markdown, tables) — the
      default `genie_summarize_answer: false`, so there is **no** extra LLM
      rewrite turn.
- [ ] Flip the flag: set `features.genie_summarize_answer: true` in
      `configuration.yaml`, reload, ask again → now the agent runs a
      summarization turn on top of Genie's answer. Set it back to `false`.
- [ ] If OBO is present, the "Open in Databricks Genie" deep link appears.

---

## 5. Data Discovery  *(verify "Accessible to me")*

- [ ] Open `/discovery` → catalog cards render; search/filter works.
- [ ] **Accessible to me** chip:
      - Deployed (OBO + warehouse): the chip is visible; toggling it filters to
        assets you can actually see in Unity Catalog (real entitlements, not
        mocked).
      - Local dev (no OBO/warehouse): the chip is **hidden** (server reports
        `available: false`) — confirm it's simply absent, not broken.
- [ ] **Certified** chip toggles correctly.
- [ ] Click a card → detail view loads.

---

## 6. My Requests & Approvals

- [ ] As **User**, kick off a request via the agent (e.g. ask for access to a
      table) and let it create a request.
- [ ] `/requests` → the request appears with correct status; open its detail.
- [ ] As **Platform/Governance Admin**, `/approvals` → pending approvals list;
      approve or reject one → status updates; the requester's `/requests`
      reflects the change.
- [ ] **Failure handling (optional):** if you can force a failing request, confirm
      it transitions to `FAILED` (not an infinite retry loop) after exhausting
      retries, and a failure row is recorded.

---

## 7. Workflows / authoring  *(Platform or Governance Admin)*

- [ ] `/build/workflows` → workflow list loads (published + drafts).
- [ ] **New workflow** button works → opens the editor.
- [ ] Open an existing workflow → **Edit** goes full-page (mirrors the graph view).
- [ ] **Authoring assistant:** open the in-page assistant. Ask it to *draft a new
      workflow* → it drafts (does **not** try to execute an existing workflow).
      The assistant drawer is draggable/resizable; chat area auto-grows; the UI
      updates when the agent completes; no duplicate text.
- [ ] Save a draft, then publish → it moves to published.
- [ ] If `workflow_authoring_locked` is true (higher envs), confirm edit/publish/
      delete are hidden and the UI steers toward bundle import.

---

## 8. Admin dashboard & tabs  *(Platform Admin)*

- [ ] `/admin` → redirects to `/admin/dashboard`; dashboard loads.
- [ ] Walk the admin tabs (whatever `ui.tabs` enables): forms/requests overview,
      feedback triage, reports, training upload, etc. Each loads without errors.

---

## 9. Governance suite  *(Platform / Governance Admin)*

- [ ] `/governance/allowlist` → list loads; create/edit an entry.
- [ ] `/governance/sentinel` → loads.
- [ ] `/governance/certification` → loads.
- [ ] `/governance/odps` → loads.
- [ ] `/governance/tags` (Tag Management) → loads; a tag change should produce a
      GitOps PR rather than a direct `ALTER TABLE` (verify the PR flow if GitHub
      is configured — see `docs/GITHUB_TOKEN_PERMISSIONS.md`).

### 9a. Context Catalog  *(changed this session — usage signal)*

- [ ] `/governance/context-catalog` → domains + documents render.
- [ ] Seed sample content if empty: `python3 backend/scripts/seed_context_catalog.py`.
- [ ] Each document row shows a **"N retrievals"** indicator; hover shows last-
      retrieved time (or "Never retrieved yet").
- [ ] **Usage tracking:** from the agent (section 3), ask a question that triggers
      `search_context_catalog` (e.g. something covered by a seeded doc). Return to
      the catalog and refresh → the matched document's retrieval count **went up**;
      browsing/searching in the admin UI itself does **not** inflate it.
- [ ] **Seed reset:** `python3 backend/scripts/seed_context_catalog.py --reset`
      removes the seeded placeholder domains (and their docs/sub-domains); the
      catalog reflects the removal.

---

## 10. Governance/FinOps agent tools via OBO  *(changed this session)*

> Best verified in a deployed env with OBO. The change makes these read-only,
> system-table tools run **as the calling user** instead of the service
> principal (falling back to SP when no token is present).

- [ ] As **Governance Admin**, ask: *"Show grants on `<catalog.schema.table>`"*,
      *"Audit my access to `<catalog>`"*, *"Search audit logs for logins this
      week"* → answers reflect what **you** can see.
- [ ] As **Finance Admin**, ask: *"What did we spend last month?"*, *"Forecast
      this month's spend"*, *"Which clusters are missing required tags?"* → all
      return data (run as your identity when OBO is present).
- [ ] Local dev (no OBO): these still work via the SP fallback — confirm no
      regressions / errors.

---

## 11. Community pages

- [ ] `/community/training` → courses render; (admin) training upload works.
- [ ] `/community/events` → events render.
- [ ] `/community/assets` → reusable assets render.
- [ ] `/community/links` → links render.

---

## 12. Reports

- [ ] `/reports` → report views load and render data.

---

## Recent-change checklist (consolidated)

Quick map of what changed this session → where it's covered:

- [ ] Sign-out button removed — §2
- [ ] "Clear my data" control — §2
- [ ] `recent_topics` redaction before LLM — §3
- [ ] Genie verbatim-answer flag (`genie_summarize_answer`) — §4
- [ ] "Accessible to me" real UC entitlements — §5
- [ ] OBO threading for governance/finops read tools — §10
- [ ] Context Catalog retrieval usage signal — §9a
- [ ] Seed script `--reset` — §9a
- [ ] Dead `form_prefill`/`route.prefill` removal — regression: route CTAs to
      community/training still work (§3, §11); no console errors.
- [ ] GitHub token permissions doc — `docs/GITHUB_TOKEN_PERMISSIONS.md` (review only)

---

## Sign-off

- [ ] No tracebacks in `backend.log` during the run.
- [ ] No errors in the browser console during the run.
- [ ] All targeted features behaved as expected (or degraded gracefully where OBO/
      Genie isn't available locally).
