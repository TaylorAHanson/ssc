import { useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import {
  Wand2,
  Plus,
  Trash2,
  Loader2,
  Save,
  Search,
  CheckCircle2,
  Circle,
  Send,
  Undo2,
  FileText,
  Workflow as WorkflowIcon,
  Copy,
  History,
  Download,
  Upload,
  ArrowLeft,
  Layers,
  Power,
  PowerOff,
  FlaskConical,
  FileWarning,
  X,
} from 'lucide-react';
import { api } from '../../services/api';
import type { Workflow, WorkflowInput, WorkflowGraphSpec, WorkflowListEvaluation, WorkflowTool } from '../../services/api';
import { WorkflowEditor } from '../../components/admin/WorkflowEditor';
import { PublishConfirmModal } from '../../components/admin/PublishConfirmModal';
import { VersionHistoryModal } from '../../components/admin/VersionHistoryModal';
import { ImportWorkflowsModal } from '../../components/admin/ImportWorkflowsModal';
import WorkflowTestsPanel from '../../components/admin/WorkflowTestsPanel';
import { useBrandingStore } from '../../stores/brandingStore';
import { Lock, Sparkles, RefreshCw } from 'lucide-react';
import { LabelWithHelp, AskAgentHint } from '../../components/ui/help-tip';
import { ChatView } from '../../components/chat/ChatView';
import { AssistantShelf } from '../../components/assistant/AssistantShelf';
import { ErrorBoundary } from '../../components/ErrorBoundary';

const inputClass =
  'w-full border border-gray-300 rounded-md h-10 px-3 text-sm focus:outline-none focus:ring-2 focus:ring-accent';
const textareaClass =
  'w-full border border-gray-300 rounded-md p-3 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-accent';

interface WorkflowFormState {
  id: string | null;
  key: string;
  name: string;
  goal: string;
  instructions_markdown: string;
  allowed_tools: string;
  policy_ref: string;
  request_type: string;
  status: string;
  graph_spec: WorkflowGraphSpec | null;
  /** The record's `updated_at` when it was loaded, sent back on save so the
   *  backend can reject a write that would overwrite someone else's newer one. */
  updated_at: string | null;
}

const emptyForm: WorkflowFormState = {
  id: null,
  key: '',
  name: '',
  goal: '',
  instructions_markdown: '',
  allowed_tools: '',
  policy_ref: '',
  request_type: '',
  status: 'draft',
  graph_spec: null,
  updated_at: null,
};

// Bookkeeping fields, not authored content: they must never count as an edit or
// be preserved over a server value.
const NON_CONTENT_FIELDS: (keyof WorkflowFormState)[] = ['id', 'status', 'updated_at'];

// "Compound" = the spec composes another workflow via a subworkflow ("Call
// workflow") stage; otherwise it's "atomic". Mirrors the backend derivation so
// the editor can badge an unsaved spec before the server round-trip.
function specComposition(spec: WorkflowGraphSpec | null | undefined): 'atomic' | 'compound' {
  const stages = spec?.stages ?? [];
  return stages.some((s) => s.kind === 'subworkflow') ? 'compound' : 'atomic';
}

// Tier -> pill styles for the at-a-glance risk/quality badges on the list.
const RISK_TIER_PILL: Record<string, string> = {
  low: 'text-green-700 bg-green-50 border-green-100',
  medium: 'text-amber-700 bg-amber-50 border-amber-100',
  high: 'text-orange-700 bg-orange-50 border-orange-100',
  critical: 'text-red-700 bg-red-50 border-red-100',
};
const QUALITY_TIER_PILL: Record<string, string> = {
  excellent: 'text-green-700 bg-green-50 border-green-100',
  good: 'text-emerald-700 bg-emerald-50 border-emerald-100',
  fair: 'text-amber-700 bg-amber-50 border-amber-100',
  poor: 'text-red-700 bg-red-50 border-red-100',
};

function EvaluationBadges({ evaluation }: { evaluation?: WorkflowListEvaluation | null }) {
  if (!evaluation) return null;
  const { risk, quality, findings } = evaluation;
  const fallback = 'text-gray-600 bg-gray-50 border-gray-200';
  return (
    <>
      <span
        className={`inline-flex items-center text-[10px] border rounded px-1.5 py-0.5 ${RISK_TIER_PILL[risk.tier] || fallback}`}
        title={`Risk ${risk.score}/100 (${risk.tier})${findings ? ` · ${findings} finding${findings === 1 ? '' : 's'}` : ''} — open Evaluate for details`}
      >
        R {risk.score}
      </span>
      <span
        className={`inline-flex items-center text-[10px] border rounded px-1.5 py-0.5 ${QUALITY_TIER_PILL[quality.tier] || fallback}`}
        title={`Quality ${quality.score}/100 (${quality.tier}) — open Evaluate for details`}
      >
        Q {quality.score}
      </span>
    </>
  );
}

/** Health, as opposed to structure. R/Q score the graph; these say whether the
 *  thing has an authored playbook and whether anyone verified it behaves — the
 *  two gaps you'd rather not discover in front of an audience. */
function HealthBadges({ workflow }: { workflow: Workflow }) {
  const src = workflow.instructions_source;
  const tests = workflow.tests_health;
  return (
    <>
      {src && src !== 'authored' && (
        <span
          className="inline-flex items-center gap-1 text-[10px] text-amber-700 bg-amber-50 border border-amber-100 rounded px-1.5 py-0.5"
          title={
            src === 'empty'
              ? 'No runtime instructions. The self-service agent has no playbook for this workflow.'
              : 'Instructions are still the auto-generated baseline — the agent has only a stub to follow.'
          }
        >
          <FileWarning className="w-3 h-3" /> {src === 'empty' ? 'no playbook' : 'stub'}
        </span>
      )}
      {tests && (
        <span
          className={`inline-flex items-center gap-1 text-[10px] border rounded px-1.5 py-0.5 ${
            tests.total === 0
              ? 'text-gray-500 bg-gray-50 border-gray-200'
              : tests.ready
                ? 'text-green-700 bg-green-50 border-green-100'
                : 'text-amber-700 bg-amber-50 border-amber-100'
          }`}
          title={
            tests.total === 0
              ? 'No behavioral tests — nothing verifies this workflow does what you expect.'
              : `${tests.passing} of ${tests.total} case(s) passing` +
                (tests.failing ? `, ${tests.failing} failing` : '') +
                (tests.never_run ? `, ${tests.never_run} never run` : '') +
                (tests.stale ? `, ${tests.stale} stale` : '')
          }
        >
          <FlaskConical className="w-3 h-3" />
          {tests.total === 0 ? 'untested' : `${tests.passing}/${tests.total}`}
        </span>
      )}
    </>
  );
}

const splitList = (value: string): string[] =>
  value
    .split(',')
    .map((v) => v.trim())
    .filter(Boolean);

// Tolerantly parse a JSON string (the model occasionally serializes tool
// arguments/results as strings). Returns null on anything non-parseable.
function safeParseJson(value: string): unknown {
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

function toForm(workflow: Workflow): WorkflowFormState {
  return {
    id: workflow.id,
    key: workflow.key,
    name: workflow.name || '',
    goal: workflow.goal || '',
    instructions_markdown: workflow.instructions_markdown || '',
    allowed_tools: (workflow.allowed_tools || []).join(', '),
    policy_ref: workflow.policy_ref || '',
    request_type: workflow.request_type || '',
    status: workflow.status,
    graph_spec: workflow.graph_spec ?? null,
    updated_at: workflow.updated_at ?? null,
  };
}

export function Workflows() {
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState('');
  const [form, setForm] = useState<WorkflowFormState>(emptyForm);
  const [saving, setSaving] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  // The selected workflow id and active tab live in the URL so the browser Back
  // button steps through workflows (and back to the list) instead of leaving the page.
  const workflowParam = searchParams.get('workflow');
  const tabParam = searchParams.get('tab');
  const tab: 'details' | 'workflow' | 'tests' =
    tabParam === 'workflow' ? 'workflow' : tabParam === 'tests' ? 'tests' : 'details';
  // Master/detail: with no workflow open we show the full-width list; opening
  // (or creating) one swaps to a full-page editor — both the Details and Graph
  // tabs get the whole canvas. A pending `new=1` flag keeps "New workflow" (and
  // agent-drafted, not-yet-saved specs) in the editor before they have an id.
  const editing = !!workflowParam || searchParams.get('new') === '1';
  const loadedIdRef = useRef<string | null>(null);
  const keyInputRef = useRef<HTMLInputElement | null>(null);
  // Bumped by "New workflow" to focus + scroll the editor into view, so the button
  // gives visible feedback even when the form was already blank.
  const [focusNewTick, setFocusNewTick] = useState(0);
  const [tools, setTools] = useState<WorkflowTool[]>([]);
  const [baseline, setBaseline] = useState<string>(() => JSON.stringify(emptyForm));
  const [publishTarget, setPublishTarget] = useState<Workflow | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [showImport, setShowImport] = useState(false);
  // In-page authoring assistant: a shared full-screen overlay shelf (see
  // AssistantShelf) that admins can ask questions / co-author in without
  // leaving the page.
  const [showAssistant, setShowAssistant] = useState(false);
  // One-step undo for assistant-driven edits, plus the banner that offers it.
  // The assistant can rewrite the graph and the instructions in a single turn;
  // without this an unwanted change had no way back short of reloading (and for
  // an unsaved draft, no way back at all).
  const [undoSnapshot, setUndoSnapshot] = useState<WorkflowFormState | null>(null);
  const [assistantNote, setAssistantNote] = useState<string | null>(null);
  // Set when a save was refused because the record changed underneath us.
  const [staleConflict, setStaleConflict] = useState(false);
  const [generatingInstructions, setGeneratingInstructions] = useState(false);
  // Bumped whenever the assistant writes test cases, so the Tests tab reloads
  // instead of showing a stale list until the admin navigates away and back.
  const [testsToken, setTestsToken] = useState(0);
  // Fields the admin has typed into since the last save/load. Tracked explicitly
  // (rather than diffed against the baseline) so it means "the human changed
  // this" and never picks up the assistant's own writes — the distinction that
  // decides whether a server value may overwrite what's on screen.
  const adminEditedRef = useRef<Set<keyof WorkflowFormState>>(new Set());
  // Set whenever the page changes its own URL (assistant write-back, save, tab
  // switch) so the loader effect can tell that apart from a human pressing Back.
  // The workflow id (or null for "back to the list") that WE are navigating to.
  // `undefined` means "we didn't initiate this", i.e. treat it as a human action.
  const programmaticNavRef = useRef<string | null | undefined>(undefined);

  // Collapse the authoring assistant when the user LEAVES the studio for the
  // workflows list. Staying within the studio — hopping between the
  // Details/Graph tabs or clicking into the editor — keeps it open
  // (click-outside close is disabled on the shelf); leaving the page entirely
  // unmounts the shelf.
  //
  // This watches the transition out of the editor, not the mere fact of not
  // editing. "Build with assistant" opens the drawer and sets `new=1` in the URL
  // in one handler, but `editing` is derived from the router's search params, so
  // there is a render where showAssistant is already true and editing is still
  // false. Closing on that condition slammed the drawer shut the instant the
  // button opened it.
  const wasEditingRef = useRef(editing);
  useEffect(() => {
    if (wasEditingRef.current && !editing && showAssistant) setShowAssistant(false);
    wasEditingRef.current = editing;
  }, [editing, showAssistant]);

  // React to the authoring assistant's tool calls so the editor on the left
  // mirrors what the agent is doing on the right:
  //   * validate / preview / save / publish all carry the drafted `graph_spec`
  //     in their *arguments* — hydrate the editor live so the admin sees the
  //     design take shape (not just a wall of chat).
  //   * save / publish additionally persist it; reload the list and open the
  //     real (id-bearing) record so subsequent edits/Save target it.
  const AUTHORING_SPEC_TOOLS = new Set([
    'validate_workflow_spec',
    'preview_workflow_spec',
    'save_workflow_draft',
    'publish_workflow',
  ]);

  // Capture the pre-change form so a single Undo can put it back.
  const snapshotForUndo = (prev: WorkflowFormState) => setUndoSnapshot(prev);

  const noteAssistantChange = (message: string) => setAssistantNote(message);

  const isFieldLocallyEdited = (field: keyof WorkflowFormState) =>
    adminEditedRef.current.has(field);

  const undoAssistantChange = () => {
    if (!undoSnapshot) return;
    setForm(undoSnapshot);
    setUndoSnapshot(null);
    setAssistantNote(null);
  };

  // Every admin-driven field change goes through this so we know which fields are
  // theirs. Functional update so a concurrent assistant write isn't dropped.
  const editForm = <K extends keyof WorkflowFormState>(
    field: K,
    value: WorkflowFormState[K],
  ) => {
    adminEditedRef.current.add(field);
    setForm((prev) => ({ ...prev, [field]: value }));
  };

  const handleAuthoringToolResult = async (
    toolName: string,
    result: unknown,
    ok: boolean,
    args?: Record<string, unknown>,
  ) => {
    // The assistant proposes behavioral tests right after saving a draft. Bump a
    // token so the Tests tab refetches, and say so — otherwise its work lands in
    // a tab the admin has no reason to open.
    if (toolName === 'save_workflow_tests') {
      if (ok) {
        setTestsToken((n) => n + 1);
        const saved = Array.isArray(args?.cases) ? (args.cases as unknown[]).length : 0;
        noteAssistantChange(
          saved > 0
            ? `Assistant proposed ${saved} test case${saved === 1 ? '' : 's'} — review them in the Tests tab, then run.`
            : 'Assistant updated the test cases — review them in the Tests tab.',
        );
      }
      return;
    }
    // The assistant can run the suite itself, so pull its verdicts into the tab
    // the admin reviews rather than leaving them only in the chat.
    if (toolName === 'run_workflow_tests') {
      if (ok) {
        setTestsToken((n) => n + 1);
        const summary = result as
          | { passed?: number; failed?: number; errored?: number; total?: number }
          | null;
        const passed = summary?.passed ?? 0;
        const total = summary?.total ?? 0;
        const bad = (summary?.failed ?? 0) + (summary?.errored ?? 0);
        noteAssistantChange(
          bad > 0
            ? `Assistant ran the tests: ${passed}/${total} passed, ${bad} need attention — see the Tests tab.`
            : `Assistant ran the tests: ${passed}/${total} passed.`,
        );
      }
      return;
    }
    if (!AUTHORING_SPEC_TOOLS.has(toolName)) return;

    // 1) Live hydrate from the call arguments (works even before a save).
    //
    // Only hydrate from a *renderable* spec (an object with at least one
    // stage). A follow-up turn sometimes calls a spec tool with a partial,
    // empty, or stage-less ``graph_spec`` (e.g. while the model is still
    // gathering an answer, or a validation/preview that errored). Overwriting
    // unconditionally there blanked the graph the admin was just looking at —
    // the reported "I answered the follow-up and the graph went away". Keeping
    // the last good graph makes the live preview sticky across turns.
    const rawSpec = args?.graph_spec ?? null;
    // The model occasionally emits ``graph_spec`` as a JSON string instead of an
    // object; parse it so the live preview (and the open-on-save below) still work.
    let spec = (typeof rawSpec === 'string'
      ? safeParseJson(rawSpec)
      : rawSpec) as WorkflowGraphSpec | null;
    // The model also sometimes emits a hole in ``stages`` (a null/undefined or
    // non-object entry). The editor and graph preview read ``stage.kind``
    // directly, so a hole throws "Cannot read properties of undefined (reading
    // 'kind')" mid-render and blanks the whole page. Drop invalid entries before
    // anything downstream touches them.
    if (spec && typeof spec === 'object' && Array.isArray(spec.stages)) {
      spec = { ...spec, stages: spec.stages.filter((s) => !!s && typeof s === 'object') };
    }
    const specHasStages =
      !!spec &&
      typeof spec === 'object' &&
      Array.isArray((spec as WorkflowGraphSpec).stages) &&
      (spec as WorkflowGraphSpec).stages.length > 0;
    if (specHasStages) {
      const argKey = typeof args?.key === 'string' ? (args.key as string) : '';
      const specName = typeof (spec as { name?: unknown }).name === 'string'
        ? ((spec as { name?: string }).name as string)
        : '';
      const argName = typeof args?.name === 'string' ? (args.name as string) : '';
      const argRt = typeof args?.request_type === 'string' ? (args.request_type as string) : '';
      const argGoal = typeof args?.goal === 'string' ? (args.goal as string) : '';
      // save_workflow_draft carries the drafted playbook in its args; mirror it
      // into the editor live, exactly like the graph, so a new workflow's
      // instructions field fills in instead of showing "just the graph".
      const argInstructions =
        typeof args?.instructions_markdown === 'string' &&
        (args.instructions_markdown as string).trim()
          ? (args.instructions_markdown as string)
          : '';
      let switchedWorkflow = false;
      setForm((prev) => {
        const targetKey = argKey || specName || prev.key;
        // If the agent is drafting a *different* workflow than what's open, start
        // clean so we don't inherit the open record's id (which would make a
        // manual Save update the wrong workflow). Resetting silently used to
        // throw away whatever the admin had open, so we snapshot for Undo and
        // tell them below.
        const differentWorkflow = !!(prev.key && targetKey && targetKey !== prev.key);
        const base = differentWorkflow ? emptyForm : prev;
        switchedWorkflow = differentWorkflow;
        snapshotForUndo(prev);
        return {
          ...base,
          key: base.key || targetKey,
          name: base.name || argName || targetKey,
          // Default request_type to the explicit arg, else the spec name, so a
          // manual Save during the design phase doesn't persist a blank type.
          request_type: base.request_type || argRt || specName,
          goal: base.goal || argGoal,
          graph_spec: spec,
          instructions_markdown: argInstructions || base.instructions_markdown,
        };
      });
      noteAssistantChange(
        switchedWorkflow
          ? `The assistant switched to a different workflow (${argKey || specName}). Your previous draft was cleared.`
          : argInstructions
            ? 'The assistant updated the workflow graph and instructions.'
            : 'The assistant updated the workflow graph.',
      );
      // Show the editor full-page so the admin watches the design take shape.
      // Before a save there's no id, so flag `new=1` to keep us in edit view.
      setStudioParams({ tab: 'workflow', isNew: true });
    }

    // 2) On a successful persist, open the editor for the saved workflow and
    //    reload the canonical record. The result payload is normally an object
    //    but parse a stringified one defensively so the open never silently skips.
    const r = (typeof result === 'string'
      ? safeParseJson(result)
      : result) as { ok?: boolean; key?: string; instructions_markdown?: string } | null;
    const persisted = toolName === 'save_workflow_draft' || toolName === 'publish_workflow';
    if (persisted && ok && r?.ok && r.key) {
      const savedKey = r.key;
      // Fill the instructions field from the authoritative save result (includes
      // the server-generated baseline when the agent passed none), so it's never
      // blank even if the canonical reload below races or misses a new draft.
      // Never clobber the admin's own unsaved wording with it: the assistant now
      // receives the open draft, so anything it saved already contains their text
      // — a differing value here means we'd be reverting an edit it didn't see.
      if (typeof r.instructions_markdown === 'string' && r.instructions_markdown.trim()) {
        setForm((prev) =>
          isFieldLocallyEdited('instructions_markdown')
            ? prev
            : { ...prev, instructions_markdown: r.instructions_markdown as string },
        );
      }
      // Switch to the editor view immediately — before the (heavier) reload — so
      // a refetch hiccup or a key→id lookup miss can't strand the admin on the
      // list. "Save a draft" should always land you on that draft's page. One
      // combined write: the tab and the "keep the editor open" floor used to be
      // two updates that could clobber each other (and the id set below).
      const keepEditorOpen = () => setStudioParams({ tab: 'workflow', isNew: true });
      keepEditorOpen();
      try {
        const fresh = await api.listWorkflows(true);
        setWorkflows(fresh);
        const match = fresh.find((w) => w.key === savedKey);
        if (match) {
          // The list endpoint omits the heavy `graph_spec` (summaries only), so
          // fetch the full record before baselining — otherwise reopening the
          // canonical workflow blanks the live graph the admin was watching.
          let full: typeof match = match;
          try {
            full = await api.getWorkflow(match.id);
          } catch {
            /* fall back to the summary; preserve the in-memory graph below */
          }
          // Set loadedIdRef === param so the URL effect doesn't re-prompt the
          // unsaved-changes guard (we just loaded the canonical version).
          loadedIdRef.current = match.id;
          setForm((prev) => {
            const nextForm = toForm(full);
            // Keep the just-hydrated graph if the reloaded record somehow lacks
            // one (e.g. summary fallback), so a save never clears the preview.
            if (!nextForm.graph_spec && prev.graph_spec) {
              nextForm.graph_spec = prev.graph_spec;
            }
            // The canonical record is the new baseline, but a field the admin had
            // edited by hand and NOT saved must survive: replacing it wholesale
            // here is what silently reverted their instructions mid-demo. Baseline
            // the server truth, then re-apply their edits on top so the field
            // still reads as unsaved and Save persists it.
            setBaseline(JSON.stringify(nextForm));
            const preserved = { ...nextForm };
            let keptAny = false;
            for (const field of adminEditedRef.current) {
              if (NON_CONTENT_FIELDS.includes(field)) continue;
              if (JSON.stringify(preserved[field]) === JSON.stringify(prev[field])) continue;
              (preserved[field] as unknown) = prev[field];
              keptAny = true;
            }
            if (keptAny) {
              setError(null);
              setAssistantNote(
                'Saved. Your unsaved edits were kept on top of the saved version — ' +
                  'click Save to persist them.',
              );
            }
            return preserved;
          });
          // Replace rather than push: the assistant saves several times in one
          // turn, and each push put another copy of this workflow in the history
          // stack for Back to walk through.
          setStudioParams({ workflow: match.id, tab: 'workflow' }, { replace: true });
        } else {
          // Couldn't resolve the id (refetch race/miss): stay in the editor on the
          // hydrated draft rather than dropping back to the list.
          keepEditorOpen();
        }
      } catch {
        // Reload failed: still keep the editor open with what we already have.
        keepEditorOpen();
      }
    }
  };
  // When true (e.g. prod), this environment locks in-place authoring: workflows
  // change only via an all-or-nothing bundle import. We hide edit/publish/delete
  // and keep inspection, dry-run, export, and import available.
  const authoringLocked = useBrandingStore((s) => s.workflowAuthoringLocked);

  const dirty = useMemo(() => JSON.stringify(form) !== baseline, [form, baseline]);

  // Which fields differ from the last saved/loaded state. Drives both the
  // "unsaved" hints in the UI and — more importantly — what we tell the authoring
  // assistant is a deliberate hand edit it must preserve.
  const unsavedFields = useMemo(() => {
    let base: WorkflowFormState;
    try {
      base = JSON.parse(baseline) as WorkflowFormState;
    } catch {
      return [];
    }
    return (Object.keys(form) as (keyof WorkflowFormState)[]).filter(
      (k) =>
        !NON_CONTENT_FIELDS.includes(k) &&
        JSON.stringify(form[k]) !== JSON.stringify(base[k]),
    );
  }, [form, baseline]);

  // What we tell the assistant is a deliberate hand edit: a field must both differ
  // from the saved version AND have been typed by the admin (not written by the
  // assistant itself, which would make the "preserve this" instruction a lie).
  const adminUnsavedFields = useMemo(
    () => unsavedFields.filter((f) => adminEditedRef.current.has(f)),
    [unsavedFields],
  );

  // The workflow the admin has open, handed to the authoring assistant on every
  // turn. Before this, the assistant's only view was `get_workflow` (which reads
  // the database), so it edited a stale copy and its save reverted whatever the
  // admin had typed but not saved. Sending the live draft — with the unsaved
  // fields called out — makes the on-screen state its starting point.
  const editorDraftContext = () => {
    if (!editing) return {};
    return {
      editor_draft: {
        key: form.key,
        name: form.name,
        goal: form.goal,
        request_type: form.request_type,
        status: form.status,
        instructions_markdown: form.instructions_markdown,
        graph_spec: form.graph_spec,
        unsaved_fields: adminUnsavedFields,
      },
    };
  };

  // Mirrors the backend's derivation (see WorkflowService.to_dict) so the badge is
  // right for unsaved text too, not just what's been persisted.
  const instructionsState: 'empty' | 'auto_baseline' | 'authored' = !form.instructions_markdown.trim()
    ? 'empty'
    : form.instructions_markdown.includes('Auto-generated from the workflow definition')
      ? 'auto_baseline'
      : 'authored';

  const generateInstructions = async () => {
    setGeneratingInstructions(true);
    setError(null);
    try {
      const result = await api.generateWorkflowInstructions({
        graph_spec: form.graph_spec,
        request_type: form.request_type.trim() || form.key.trim() || null,
        goal: form.goal.trim() || null,
        name: form.name.trim() || null,
        // Sent so the generator improves the admin's wording instead of
        // discarding it. A baseline is not "their wording", so it isn't sent.
        existing_instructions:
          instructionsState === 'authored' ? form.instructions_markdown : null,
      });
      snapshotForUndo(form);
      editForm('instructions_markdown', result.instructions_markdown);
      const quality = result.quality;
      noteAssistantChange(
        [
          result.source === 'llm'
            ? 'Authored a playbook from the graph and goal.'
            : 'Generated the graph-derived baseline.',
          result.warning,
          quality ? `Instruction quality: ${quality.score}/100 (${quality.tier}).` : null,
          'Review it — the Assumptions and Open Questions sections are for you to correct.',
        ]
          .filter(Boolean)
          .join(' '),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to generate instructions');
    } finally {
      setGeneratingInstructions(false);
    }
  };

  const setFormBaselined = (next: WorkflowFormState) => {
    setForm(next);
    setBaseline(JSON.stringify(next));
    // Loading or saving makes the on-screen values the saved values, so there are
    // no outstanding hand edits to protect any more.
    adminEditedRef.current = new Set();
    setUndoSnapshot(null);
    setAssistantNote(null);
  };

  const setTab = (next: 'details' | 'workflow' | 'tests') => {
    setStudioParams({ tab: next });
  };

  // ONE atomic write for the studio's view state (which workflow is open, whether
  // it's an unsaved draft, and the active tab).
  //
  // Several `setSearchParams` calls in the same tick are a silent clobber:
  // react-router's functional updater reads the CURRENT location every time, so
  // two updaters queued together both start from the same base and the last one
  // wins. A single assistant save used to issue up to four of them (hydrate,
  // setTab, keepEditorOpen, setWorkflowParam) — when the loser carried
  // `workflow=<id>`, the URL came back with only `new=1`, which the loader effect
  // below reads as "the admin left this workflow". That raised a blocking native
  // "You have unsaved changes. Discard them?" over the assistant's own write and
  // then blanked the editor behind it.
  const setStudioParams = (
    next: {
      workflow?: string | null;
      isNew?: boolean;
      tab?: 'details' | 'workflow' | 'tests';
    },
    opts: { replace?: boolean } = { replace: true },
  ) => {
    // Anything the page navigates to itself is trusted: the "unsaved changes"
    // guard exists for a human using Back/Forward, not for our own view sync.
    //
    // Trust is armed with the exact workflow we're switching to, and only when
    // we're actually switching. A bare boolean stayed armed forever after any
    // call that left `workflow` alone (a tab click is one), because the guard
    // lives in an effect keyed on `workflow` that never ran to consume it — so
    // the next time the admin really did navigate away with unsaved edits, the
    // prompt was skipped and the form was silently blanked.
    if (next.workflow !== undefined) programmaticNavRef.current = next.workflow;
    setSearchParams((prev) => {
      const params = new URLSearchParams(prev);
      if (next.workflow !== undefined) {
        if (next.workflow) {
          params.set('workflow', next.workflow);
          params.delete('new');
        } else {
          params.delete('workflow');
        }
      }
      if (next.isNew === true && !params.get('workflow')) params.set('new', '1');
      if (next.isNew === false) params.delete('new');
      if (next.tab) params.set('tab', next.tab);
      return params;
    }, opts);
  };

  const setWorkflowParam = (id: string | null, opts: { replace?: boolean } = {}) =>
    setStudioParams({ workflow: id }, opts);

  // Leave the full-page editor and return to the list. Resets the form first so
  // the URL effect (which also guards unsaved edits) doesn't double-prompt.
  const backToList = () => {
    if (!confirmDiscard()) return;
    loadedIdRef.current = null;
    setFormBaselined(emptyForm);
    setSearchParams(
      (prev) => {
        const params = new URLSearchParams(prev);
        params.delete('workflow');
        params.delete('new');
        params.delete('tab');
        return params;
      },
      { replace: true },
    );
  };

  const loadList = async () => {
    setLoading(true);
    setError(null);
    try {
      setWorkflows(await api.listWorkflows(true));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load workflows');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadList();
    api
      .listWorkflowTools()
      .then(setTools)
      .catch(() => setTools([]));
  }, []);

  useEffect(() => {
    if (!dirty) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [dirty]);

  // Load the workflow named in the URL (deep links + Back/Forward navigation).
  useEffect(() => {
    if (workflowParam === loadedIdRef.current) return;
    // Guard unsaved edits before swapping workflows (covers Back/Forward too) —
    // but ONLY for navigation a human performed. `confirm()` is synchronous and
    // blocks the whole page, so raising it here while the assistant is streaming
    // froze the studio mid-turn: the admin couldn't even close the shelf, and
    // whichever way they answered, an in-flight save was already writing state
    // underneath the dialog.
    const wasProgrammatic = programmaticNavRef.current === workflowParam;
    programmaticNavRef.current = undefined;
    if (!wasProgrammatic && !confirmDiscard()) {
      setWorkflowParam(loadedIdRef.current, { replace: true });
      return;
    }
    if (!workflowParam) {
      loadedIdRef.current = null;
      // `new=1` means an unsaved draft is open — often one the assistant is still
      // writing. Blanking the form here would throw that work away.
      if (searchParams.get('new') !== '1') setFormBaselined(emptyForm);
      return;
    }
    loadedIdRef.current = workflowParam;
    api
      .getWorkflow(workflowParam)
      .then((full) => setFormBaselined(toForm(full)))
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load workflow'));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflowParam]);

  // After "New workflow", focus the Key field and bring the editor into view.
  useEffect(() => {
    if (focusNewTick === 0) return;
    const el = keyInputRef.current;
    if (!el) return;
    el.focus();
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }, [focusNewTick]);

  const filtered = useMemo(() => {
    const f = filter.trim().toLowerCase();
    if (!f) return workflows;
    return workflows.filter(
      (s) =>
        s.key.toLowerCase().includes(f) ||
        (s.name || '').toLowerCase().includes(f) ||
        (s.goal || '').toLowerCase().includes(f),
    );
  }, [workflows, filter]);

  const selectedWorkflow = useMemo(
    () => workflows.find((s) => s.id === form.id) ?? null,
    [workflows, form.id],
  );

  const confirmDiscard = () =>
    !dirty || confirm('You have unsaved changes. Discard them?');

  const selectWorkflow = (workflow: Workflow) => {
    // Skip only if this row is already the *open* workflow (URL-driven). Comparing
    // against `form.id` was buggy: right after an agent save the form holds the new
    // draft's id while the list is still showing, so clicking that row early-returned
    // and appeared "dead" until a refresh cleared the form.
    if (workflow.id === workflowParam) return;
    // Push a history entry so Back returns to the list, then let the URL effect
    // load it (and prompt about unsaved edits in one place).
    setWorkflowParam(workflow.id);
  };

  const startNew = () => {
    // Reset directly rather than relying on the URL effect: when no workflow is
    // selected, `workflow` is already absent, so deleting it wouldn't change
    // `workflowParam` and the effect would never fire (button looks dead).
    if (!confirmDiscard()) return;
    loadedIdRef.current = null;
    setFormBaselined(emptyForm);
    setSearchParams((prev) => {
      const params = new URLSearchParams(prev);
      params.delete('workflow');
      params.set('new', '1');
      params.set('tab', 'details');
      return params;
    });
    // Always give visible feedback (focus + scroll), even if the form was
    // already blank so no state/URL actually changed.
    setFocusNewTick((t) => t + 1);
  };

  // Start from a conversation instead of a blank form: open a fresh draft with the
  // assistant already expanded, so the first thing an author does is describe the
  // workflow rather than guess at gate types.
  const buildWithAssistant = () => {
    if (!confirmDiscard()) return;
    loadedIdRef.current = null;
    setFormBaselined(emptyForm);
    setSearchParams((prev) => {
      const params = new URLSearchParams(prev);
      params.delete('workflow');
      params.set('new', '1');
      params.set('tab', 'details');
      return params;
    });
    setShowAssistant(true);
  };

  const buildInput = (): WorkflowInput => ({
    key: form.key.trim(),
    name: form.name.trim() || form.key.trim(),
    goal: form.goal.trim() || null,
    instructions_markdown: form.instructions_markdown,
    allowed_tools: form.allowed_tools.trim() ? splitList(form.allowed_tools) : null,
    policy_ref: form.policy_ref.trim() || null,
    request_type: form.request_type.trim() || null,
    // Status is not editable here — publishing goes through Publish so the
    // pre-publish checks and version snapshot actually run.
    status: form.status,
    graph_spec: form.graph_spec,
    if_unmodified_since: form.updated_at,
  });

  const save = async () => {
    if (!form.key.trim()) {
      setError('Key is required');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const wasNew = !form.id;
      const saved = form.id
        ? await api.updateWorkflow(form.id, buildInput())
        : await api.createWorkflow(buildInput());
      loadedIdRef.current = saved.id;
      setFormBaselined(toForm(saved));
      if (wasNew) setWorkflowParam(saved.id, { replace: true });
      await loadList();
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Failed to save workflow';
      setError(message);
      // A rejected stale write leaves the edits on screen; offer the reload
      // explicitly so "changed underneath you" doesn't read as "your work is
      // gone". Nothing is discarded until they choose to reload.
      if (/changed since you loaded it/i.test(message) && form.id) {
        setStaleConflict(true);
      }
    } finally {
      setSaving(false);
    }
  };

  // Reload the server's copy after a stale-write rejection, keeping the admin's
  // own edits on top so the save can be retried without retyping.
  const reloadAfterConflict = async () => {
    if (!form.id) return;
    setSaving(true);
    try {
      const full = await api.getWorkflow(form.id);
      const fresh = toForm(full);
      const edited = new Set(adminEditedRef.current);
      setBaseline(JSON.stringify(fresh));
      setForm((prev) => {
        const merged = { ...fresh };
        for (const field of edited) {
          if (NON_CONTENT_FIELDS.includes(field)) continue;
          (merged[field] as unknown) = prev[field];
        }
        return merged;
      });
      setStaleConflict(false);
      setError(null);
      setAssistantNote(
        'Reloaded the current version and kept your edits on top. Review, then Save.',
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to reload workflow');
    } finally {
      setSaving(false);
    }
  };

  const togglePublish = async (workflow: Workflow) => {
    setBusyId(workflow.id);
    setError(null);
    try {
      const updated =
        workflow.status === 'published'
          ? await api.unpublishWorkflow(workflow.id)
          : await api.publishWorkflow(workflow.id);
      if (form.id === workflow.id) setFormBaselined(toForm(updated));
      await loadList();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to change status');
    } finally {
      setBusyId(null);
    }
  };

  // Operational kill switch: turn a workflow off/on without editing or
  // unpublishing it. Stays available even when authoring is locked (prod), so we
  // don't touch the form definition here — just reload so badges reflect state.
  const toggleDisabled = async (workflow: Workflow) => {
    setBusyId(workflow.id);
    setError(null);
    try {
      await api.setWorkflowDisabled(workflow.id, !workflow.disabled);
      await loadList();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to change workflow state');
    } finally {
      setBusyId(null);
    }
  };

  const clone = async (workflow: Workflow) => {
    setBusyId(workflow.id);
    setError(null);
    try {
      const created = await api.cloneWorkflow(workflow.id);
      await loadList();
      const full = await api.getWorkflow(created.id);
      loadedIdRef.current = created.id;
      setFormBaselined(toForm(full));
      setSearchParams((prev) => {
        const params = new URLSearchParams(prev);
        params.set('workflow', created.id);
        params.delete('new');
        params.set('tab', 'details');
        return params;
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to duplicate workflow');
    } finally {
      setBusyId(null);
    }
  };

  const exportBundle = async (opts: { ids?: string[]; publishedOnly?: boolean } = {}) => {
    setError(null);
    try {
      const bundle = await api.exportWorkflowsBundle(opts);
      const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `workflows-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to export workflows');
    }
  };

  const remove = async (workflow: Workflow) => {
    if (!confirm(`Delete workflow "${workflow.key}"? This cannot be undone.`)) return;
    setBusyId(workflow.id);
    setError(null);
    try {
      await api.deleteWorkflow(workflow.id);
      if (form.id === workflow.id) {
        loadedIdRef.current = null;
        setFormBaselined(emptyForm);
        setWorkflowParam(null, { replace: true });
      }
      await loadList();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to delete workflow');
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-heading flex items-center gap-2">
            <Wand2 className="w-6 h-6 text-accent" />
            Workflow Studio
          </h1>
          <p className="text-sm text-gray-500 mt-1 max-w-3xl">
            Author the agent's capabilities as data. Each published workflow appears in the
            agent's capabilities list and is returned by <code>get_workflow_instructions</code>.
            Edit and publish here instead of changing instruction files and redeploying.
          </p>
          <div className="mt-2 flex items-center gap-4">
            <a
              href="/governance/context-catalog"
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-gray-400 hover:text-accent hover:underline"
            >
              Read the authoring guide
            </a>
            <AskAgentHint onClick={() => setShowAssistant((v) => !v)} />
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button onClick={() => exportBundle()} variant="outline" title="Export all workflows as a portable bundle">
            <Download className="w-4 h-4 mr-1" /> Export
          </Button>
          <Button onClick={() => setShowImport(true)} variant="outline" title="Import a bundle from another environment">
            <Upload className="w-4 h-4 mr-1" /> Import
          </Button>
          {!authoringLocked && (
            <>
              <Button onClick={startNew} variant="outline">
                <Plus className="w-4 h-4 mr-1" /> New workflow
              </Button>
              {/* Authoring from a blank form is the harder road; describing the
                  workflow out loud is the one that actually produces a playbook. */}
              <Button
                onClick={buildWithAssistant}
                title="Describe the workflow in plain language and let the assistant draft the graph, instructions, and tests"
              >
                <Sparkles className="w-4 h-4 mr-1" /> Build with assistant
              </Button>
            </>
          )}
        </div>
      </div>

      {authoringLocked && (
        <div className="bg-amber-50 border border-amber-200 text-amber-800 rounded-md px-4 py-3 text-sm flex items-start gap-2">
          <Lock className="w-4 h-4 mt-0.5 shrink-0" />
          <div>
            <span className="font-medium">Workflow authoring is locked in this environment.</span>{' '}
            Workflows are read-only here — you can inspect, dry-run, and export them, but
            creating, editing, publishing, and deleting are disabled. To change a workflow,
            build and publish it in a lower environment, then promote it as an all-or-nothing
            bundle via <span className="font-medium">Import</span>. You can still{' '}
            <span className="font-medium">turn individual workflows on/off</span> here — an
            operational switch that hides a workflow from the agent without changing its
            definition.
          </div>
        </div>
      )}

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 rounded-md px-4 py-2 text-sm flex items-center justify-between gap-3">
          <span>{error}</span>
          {staleConflict && (
            <Button variant="outline" size="sm" onClick={reloadAfterConflict} disabled={saving}>
              <RefreshCw className={`w-3.5 h-3.5 mr-1 ${saving ? 'animate-spin' : ''}`} />
              Reload and keep my edits
            </Button>
          )}
        </div>
      )}

      {/* What the assistant just changed, with one-step Undo. The assistant can
          rewrite the graph and the instructions in a single turn, so a change the
          admin didn't want needs a way back that doesn't involve a reload. */}
      {assistantNote && (
        <div className="bg-blue-50 border border-blue-200 text-blue-800 rounded-md px-4 py-2 text-sm flex items-center justify-between gap-3">
          <span className="inline-flex items-center gap-2">
            <Sparkles className="w-4 h-4 shrink-0" />
            {assistantNote}
          </span>
          <span className="flex items-center gap-1 shrink-0">
            {undoSnapshot && (
              <Button variant="ghost" size="sm" onClick={undoAssistantChange}>
                <Undo2 className="w-3.5 h-3.5 mr-1" /> Undo
              </Button>
            )}
            <button
              type="button"
              className="text-blue-400 hover:text-blue-700 p-1"
              title="Dismiss"
              onClick={() => setAssistantNote(null)}
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </span>
        </div>
      )}

      <div className="grid grid-cols-1 gap-6">
        {/* List — the full-width "master" view shown when no workflow is open. */}
        <Card className={editing ? 'hidden' : ''}>
          <CardHeader>
            <CardTitle className="text-base">All workflows</CardTitle>
            <CardDescription>{workflows.length} total</CardDescription>
            <div className="relative mt-2">
              <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
              <input
                className={`${inputClass} pl-9`}
                placeholder="Filter workflows..."
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
              />
            </div>
          </CardHeader>
          <CardContent className="max-h-[60vh] overflow-y-auto space-y-1">
            {loading ? (
              <div className="flex items-center justify-center py-8 text-gray-400">
                <Loader2 className="w-5 h-5 animate-spin" />
              </div>
            ) : (
              filtered.map((s) => (
                <div
                  key={s.id}
                  className={`flex items-center justify-between gap-2 rounded-md px-2 py-2 cursor-pointer hover:bg-gray-50 ${
                    form.id === s.id ? 'bg-accent/10' : ''
                  }`}
                  onClick={() => selectWorkflow(s)}
                >
                  <div className="min-w-0">
                    <div className="text-sm font-medium truncate">{s.key}</div>
                    <div className="text-xs text-gray-500 truncate">{s.goal || '—'}</div>
                    {s.last_published_at && (
                      <div className="text-[10px] text-gray-400">
                        published {new Date(s.last_published_at).toLocaleDateString()}
                      </div>
                    )}
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    <HealthBadges workflow={s} />
                    <EvaluationBadges evaluation={s.evaluation} />
                    {s.composition === 'compound' && (
                      <span
                        className="inline-flex items-center gap-1 text-[10px] text-indigo-700 bg-indigo-50 border border-indigo-100 rounded px-1.5 py-0.5"
                        title={
                          s.subworkflow_refs?.length
                            ? `Compound workflow — composes: ${s.subworkflow_refs.join(', ')}`
                            : 'Compound workflow (composes nested workflows)'
                        }
                      >
                        <Layers className="w-3 h-3" /> compound
                      </span>
                    )}
                    {s.disabled ? (
                      <span
                        className="inline-flex items-center gap-1 text-[11px] text-amber-700 bg-amber-50 border border-amber-100 rounded px-1.5 py-0.5"
                        title="Turned off — hidden from the agent (definition preserved)"
                      >
                        <PowerOff className="w-3 h-3" /> off
                      </span>
                    ) : s.status === 'published' ? (
                      <span className="inline-flex items-center gap-1 text-[11px] text-green-700">
                        <CheckCircle2 className="w-3 h-3" /> live
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 text-[11px] text-gray-400">
                        <Circle className="w-3 h-3" /> draft
                      </span>
                    )}
                    {/* On/off kill switch — available even when authoring is locked. */}
                    <button
                      type="button"
                      title={
                        s.disabled
                          ? 'Turn on — show this workflow to the agent'
                          : 'Turn off — hide this workflow from the agent (keeps the definition)'
                      }
                      className={`p-0.5 ${
                        s.disabled
                          ? 'text-amber-500 hover:text-green-600'
                          : 'text-green-500 hover:text-amber-600'
                      }`}
                      disabled={busyId === s.id}
                      onClick={(e) => {
                        e.stopPropagation();
                        toggleDisabled(s);
                      }}
                    >
                      {busyId === s.id ? (
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      ) : s.disabled ? (
                        <PowerOff className="w-3.5 h-3.5" />
                      ) : (
                        <Power className="w-3.5 h-3.5" />
                      )}
                    </button>
                    {!authoringLocked && (
                      <button
                        type="button"
                        title="Duplicate"
                        className="text-gray-300 hover:text-accent p-0.5"
                        disabled={busyId === s.id}
                        onClick={(e) => {
                          e.stopPropagation();
                          clone(s);
                        }}
                      >
                        <Copy className="w-3.5 h-3.5" />
                      </button>
                    )}
                  </div>
                </div>
              ))
            )}
            {!loading && filtered.length === 0 && (
              <div className="text-sm text-gray-400 py-6 text-center">No workflows found.</div>
            )}
          </CardContent>
        </Card>

        {/* Editor — the full-page "detail" view. Both inner tabs (Details and
            Graph) get the whole width. */}
        <Card className={editing ? '' : 'hidden'}>
          <CardHeader>
            <div className="flex items-start justify-between gap-3">
              <div className="flex items-start gap-2 min-w-0">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={backToList}
                  title="Back to all workflows"
                  className="shrink-0 -ml-2 text-gray-500"
                >
                  <ArrowLeft className="w-4 h-4 mr-1" /> Workflow Studio
                </Button>
                <div className="min-w-0">
                <CardTitle className="text-base">
                  {form.id ? `Edit: ${form.key}` : 'New workflow'}
                </CardTitle>
                <CardDescription>
                  {form.id ? (
                    <span className="inline-flex items-center gap-2">
                      <span
                        className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] ${
                          form.status === 'published'
                            ? 'bg-green-50 text-green-700'
                            : 'bg-gray-100 text-gray-500'
                        }`}
                      >
                        {form.status === 'published' ? 'live' : 'draft'}
                      </span>
                      {selectedWorkflow?.disabled && (
                        <span
                          className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] bg-amber-50 text-amber-700"
                          title="Turned off — hidden from the agent (definition preserved)"
                        >
                          <PowerOff className="w-3 h-3" /> off
                        </span>
                      )}
                      <span
                        className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] ${
                          specComposition(form.graph_spec) === 'compound'
                            ? 'bg-indigo-50 text-indigo-700'
                            : 'bg-gray-100 text-gray-500'
                        }`}
                        title={
                          specComposition(form.graph_spec) === 'compound'
                            ? 'Compound — composes nested workflows via a Call workflow stage'
                            : 'Atomic — only gates and steps (no nested workflows)'
                        }
                      >
                        <Layers className="w-3 h-3" /> {specComposition(form.graph_spec)}
                      </span>
                      {selectedWorkflow &&
                        selectedWorkflow.status === 'published' &&
                        selectedWorkflow.composition &&
                        selectedWorkflow.composition !== specComposition(form.graph_spec) && (
                          <span
                            className="text-[11px] text-amber-600"
                            title={`The live workflow is ${selectedWorkflow.composition}, but your unsaved edits make it ${specComposition(form.graph_spec)}. Publish to apply.`}
                          >
                            · differs from live ({selectedWorkflow.composition})
                          </span>
                        )}
                      {selectedWorkflow && <span>v{selectedWorkflow.version}</span>}
                      {selectedWorkflow?.updated_at && (
                        <span className="text-gray-400">
                          · edited {new Date(selectedWorkflow.updated_at).toLocaleDateString()}
                          {selectedWorkflow.created_by ? ` by ${selectedWorkflow.created_by}` : ''}
                        </span>
                      )}
                    </span>
                  ) : (
                    'Define a new agent capability.'
                  )}
                </CardDescription>
                </div>
              </div>
              {form.id && (
                <div className="flex items-center gap-1 shrink-0">
                  {selectedWorkflow && (
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={busyId === selectedWorkflow.id}
                      onClick={() => toggleDisabled(selectedWorkflow)}
                      title={
                        selectedWorkflow.disabled
                          ? 'Turn this workflow on (show it to the agent)'
                          : 'Turn this workflow off (hide it from the agent; keeps the definition)'
                      }
                      className={selectedWorkflow.disabled ? 'text-green-700' : 'text-amber-700'}
                    >
                      {busyId === selectedWorkflow.id ? (
                        <Loader2 className="w-4 h-4 mr-1 animate-spin" />
                      ) : selectedWorkflow.disabled ? (
                        <Power className="w-4 h-4 mr-1" />
                      ) : (
                        <PowerOff className="w-4 h-4 mr-1" />
                      )}
                      {selectedWorkflow.disabled ? 'Turn on' : 'Turn off'}
                    </Button>
                  )}
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setShowHistory(true)}
                    title="View version history"
                  >
                    <History className="w-4 h-4 mr-1" /> History
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => form.id && exportBundle({ ids: [form.id] })}
                    title="Export this workflow"
                  >
                    <Download className="w-4 h-4" />
                  </Button>
                  {!authoringLocked && (
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={busyId === form.id}
                      onClick={() => {
                        const s = workflows.find((x) => x.id === form.id);
                        if (s) clone(s);
                      }}
                      title="Duplicate as a new draft"
                    >
                      <Copy className="w-4 h-4 mr-1" /> Duplicate
                    </Button>
                  )}
                </div>
              )}
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center gap-1 border-b border-gray-200 -mt-2">
              <button
                type="button"
                onClick={() => setTab('details')}
                className={`inline-flex items-center gap-1.5 px-3 py-2 text-sm border-b-2 -mb-px ${
                  tab === 'details'
                    ? 'border-accent text-accent font-medium'
                    : 'border-transparent text-gray-500 hover:text-gray-700'
                }`}
              >
                <FileText className="w-4 h-4" /> Details & instructions
              </button>
              <button
                type="button"
                onClick={() => setTab('workflow')}
                className={`inline-flex items-center gap-1.5 px-3 py-2 text-sm border-b-2 -mb-px ${
                  tab === 'workflow'
                    ? 'border-accent text-accent font-medium'
                    : 'border-transparent text-gray-500 hover:text-gray-700'
                }`}
              >
                <WorkflowIcon className="w-4 h-4" /> Workflow graph
                {form.graph_spec && form.graph_spec.stages.length > 0 && (
                  <span className="ml-1 text-[10px] bg-accent/10 text-accent rounded-full px-1.5 py-0.5">
                    {form.graph_spec.stages.length}
                  </span>
                )}
              </button>
              <button
                type="button"
                onClick={() => setTab('tests')}
                className={`inline-flex items-center gap-1.5 px-3 py-2 text-sm border-b-2 -mb-px ${
                  tab === 'tests'
                    ? 'border-accent text-accent font-medium'
                    : 'border-transparent text-gray-500 hover:text-gray-700'
                }`}
              >
                <FlaskConical className="w-4 h-4" /> Tests
              </button>
            </div>

            {tab === 'details' && (
            <>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <LabelWithHelp
                  className="text-xs font-medium text-gray-600 mb-1"
                  help="Stable internal identifier the agent and request types reference. Lowercase with underscores (e.g. workspace_access). Can't be changed after creation."
                >
                  Key (internal name) *
                </LabelWithHelp>
                <input
                  ref={keyInputRef}
                  className={inputClass}
                  placeholder="e.g. workspace_access"
                  value={form.key}
                  disabled={!!form.id || authoringLocked}
                  onChange={(e) => editForm('key', e.target.value)}
                />
              </div>
              <div>
                <LabelWithHelp
                  className="text-xs font-medium text-gray-600 mb-1"
                  help="Friendly display name shown to users in the agent's capabilities list. Defaults to the key if left blank."
                >
                  Name
                </LabelWithHelp>
                <input
                  className={inputClass}
                  placeholder="Human-readable name"
                  value={form.name}
                  disabled={authoringLocked}
                  onChange={(e) => editForm('name', e.target.value)}
                />
              </div>
            </div>

            <div>
              <LabelWithHelp
                className="text-xs font-medium text-gray-600 mb-1"
                help="One sentence describing what this workflow does. The agent uses it to decide when this capability is relevant to a user's request."
              >
                Goal (one-line capability description)
              </LabelWithHelp>
              <input
                className={inputClass}
                placeholder="Request access to an existing Databricks workspace."
                value={form.goal}
                disabled={authoringLocked}
                onChange={(e) => editForm('goal', e.target.value)}
              />
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div>
                <LabelWithHelp
                  className="text-xs font-medium text-gray-600 mb-1"
                  help="Optional OPA policy package that governs this workflow's tool calls (e.g. data.agent.tools). Leave blank to use the default policy."
                >
                  Policy ref
                </LabelWithHelp>
                <input
                  className={inputClass}
                  placeholder="data.agent.tools"
                  value={form.policy_ref}
                  disabled={authoringLocked}
                  onChange={(e) => editForm('policy_ref', e.target.value)}
                />
              </div>
              <div>
                <LabelWithHelp
                  className="text-xs font-medium text-gray-600 mb-1"
                  help="The kind of request this workflow governs. A published workflow only runs for requests of this type — required before publishing, or the graph won't run."
                >
                  Request type
                </LabelWithHelp>
                <input
                  className={inputClass}
                  placeholder="workspace_access"
                  value={form.request_type}
                  disabled={authoringLocked}
                  onChange={(e) => editForm('request_type', e.target.value)}
                />
              </div>
              <div>
                <LabelWithHelp
                  className="text-xs font-medium text-gray-600 mb-1"
                  help="Draft = saved but not live; safe to edit and dry-run. Published = live and governing real requests. Change this with Publish / Unpublish — that path runs the pre-publish checks and snapshots a version you can roll back to."
                >
                  Status
                </LabelWithHelp>
                {/* Read-only on purpose. Setting this to "published" and saving used
                    to make a workflow live while skipping validation, the compile
                    check, the version bump and the snapshot — everything Publish
                    enforces. */}
                <div className={`${inputClass} bg-gray-50 text-gray-600 flex items-center justify-between`}>
                  <span>{form.status === 'published' ? 'published (live)' : 'draft'}</span>
                  <span className="text-[10px] text-gray-400">use Publish to change</span>
                </div>
              </div>
            </div>

            <div>
              <LabelWithHelp
                className="text-xs font-medium text-gray-600 mb-1"
                help="Capability scope: the only mutating tools this workflow is allowed to call. Anything outside this list is refused at runtime. Leave blank to use the agent's default tool set."
              >
                Allowed tools (capability scoping, comma-separated; blank = defaults)
              </LabelWithHelp>
              <input
                className={inputClass}
                placeholder="get_target_workspaces, execute_workflow"
                value={form.allowed_tools}
                disabled={authoringLocked}
                onChange={(e) => editForm('allowed_tools', e.target.value)}
              />
            </div>

            <div>
              <div className="flex items-end justify-between gap-3 mb-1">
                <LabelWithHelp
                  className="text-xs font-medium text-gray-600"
                  help="Markdown instructions the agent follows when running this workflow: the goal, what information to gather, and how to behave. This is the prompt guidance, separate from the executable workflow graph."
                >
                  Instructions (markdown)
                </LabelWithHelp>
                <div className="flex items-center gap-2 shrink-0">
                  {/* The runtime agent follows this text, so "nobody has written it"
                      and "this is still the generated stub" are both worth seeing
                      before publishing. The signal existed server-side but was
                      never surfaced. */}
                  {instructionsState === 'empty' && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-50 text-red-700 border border-red-100">
                      no instructions
                    </span>
                  )}
                  {instructionsState === 'auto_baseline' && (
                    <span
                      className="text-[10px] px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 border border-amber-100"
                      title="These are auto-generated from the graph, so they only cover the inputs your steps reference. Edit or regenerate before publishing."
                    >
                      auto-generated baseline
                    </span>
                  )}
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={authoringLocked || generatingInstructions}
                    onClick={generateInstructions}
                    title="Author a full playbook from the graph and goal. Your existing text is used as the starting point, not replaced."
                  >
                    {generatingInstructions ? (
                      <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" />
                    ) : (
                      <Sparkles className="w-3.5 h-3.5 mr-1" />
                    )}
                    {form.instructions_markdown.trim() && instructionsState !== 'auto_baseline'
                      ? 'Improve instructions'
                      : 'Generate instructions'}
                  </Button>
                </div>
              </div>
              <textarea
                className={textareaClass}
                rows={16}
                placeholder="# Workflow Instructions&#10;&#10;**Goal**: ...&#10;&#10;## Information to Gather&#10;..."
                value={form.instructions_markdown}
                disabled={authoringLocked}
                onChange={(e) => editForm('instructions_markdown', e.target.value)}
              />
            </div>
            </>
            )}

            {tab === 'workflow' && (
              <div>
                {form.graph_spec ? (
                  <ErrorBoundary
                    label="the workflow editor"
                    resetKeys={[workflowParam, searchParams.get('new')]}
                  >
                    <WorkflowEditor
                      spec={form.graph_spec}
                      tools={tools}
                      onChange={(graph_spec) => editForm('graph_spec', graph_spec)}
                      onAskAgent={() => setShowAssistant(true)}
                      instructionsMarkdown={form.instructions_markdown}
                    />
                  </ErrorBoundary>
                ) : (
                  // Assistant-first: an empty canvas is the least useful thing to
                  // hand someone who has never authored a workflow, so describing
                  // it in words is the primary path and the blank graph the escape
                  // hatch.
                  <div className="text-center py-10 border border-dashed border-gray-200 rounded-lg">
                    <WorkflowIcon className="w-8 h-8 text-gray-300 mx-auto mb-3" />
                    <p className="text-sm text-gray-600 mb-1">No workflow graph yet</p>
                    <p className="text-xs text-gray-400 max-w-md mx-auto mb-4">
                      A workflow is ordered approval gates and provisioning steps. Describe
                      what should happen in plain language and the assistant will draft the
                      graph, the runtime instructions, and a few test cases — then you edit
                      from there.
                    </p>
                    <div className="flex items-center justify-center gap-2">
                      <Button onClick={() => setShowAssistant(true)}>
                        <Sparkles className="w-4 h-4 mr-1" /> Describe it to the assistant
                      </Button>
                      <Button
                        variant="outline"
                        onClick={() =>
                          editForm('graph_spec', {
                            name: form.key.trim() || form.request_type.trim() || 'workflow',
                            completed_status: 'completed',
                            complete_fact: null,
                            stages: [],
                          })
                        }
                      >
                        <Plus className="w-4 h-4 mr-1" /> Start from a blank graph
                      </Button>
                    </div>
                  </div>
                )}
              </div>
            )}

            {tab === 'tests' && (
              <ErrorBoundary label="the tests tab" resetKeys={[workflowParam]}>
                <WorkflowTestsPanel
                  workflowId={workflowParam}
                  dirty={dirty}
                  reloadToken={testsToken}
                  onAskAgent={() => setShowAssistant(true)}
                />
              </ErrorBoundary>
            )}

            {authoringLocked ? (
              <div className="flex items-center gap-2 pt-2 text-xs text-gray-500">
                <Lock className="w-3.5 h-3.5" />
                Read-only in this environment. Promote changes via Import (bundle).
              </div>
            ) : (
              <div className="flex items-center gap-2 pt-2">
                <Button onClick={save} disabled={saving || !dirty}>
                  {saving ? (
                    <Loader2 className="w-4 h-4 mr-1 animate-spin" />
                  ) : (
                    <Save className="w-4 h-4 mr-1" />
                  )}
                  Save
                </Button>
                {dirty && (
                  <span className="text-xs text-amber-600 inline-flex items-center gap-1">
                    Unsaved changes
                  </span>
                )}
                {form.id && (
                  <>
                    <Button
                      variant="outline"
                      disabled={busyId === form.id}
                      onClick={() => {
                        const s = workflows.find((x) => x.id === form.id);
                        if (!s) return;
                        if (s.status === 'published') {
                          togglePublish(s);
                          return;
                        }
                        if (dirty) {
                          setError('Save your changes before publishing.');
                          return;
                        }
                        setError(null);
                        setPublishTarget(s);
                      }}
                    >
                      {form.status === 'published' ? (
                        <>
                          <Undo2 className="w-4 h-4 mr-1" /> Unpublish
                        </>
                      ) : (
                        <>
                          <Send className="w-4 h-4 mr-1" /> Publish
                        </>
                      )}
                    </Button>
                    <Button
                      variant="outline"
                      className="text-red-600 hover:bg-red-50"
                      disabled={busyId === form.id}
                      onClick={() => {
                        const s = workflows.find((x) => x.id === form.id);
                        if (s) remove(s);
                      }}
                    >
                      <Trash2 className="w-4 h-4 mr-1" /> Delete
                    </Button>
                  </>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {publishTarget && (
        <PublishConfirmModal
          workflow={publishTarget}
          graphSpec={form.graph_spec}
          tools={tools}
          onClose={() => setPublishTarget(null)}
          onConfirm={async () => {
            await togglePublish(publishTarget);
            setPublishTarget(null);
          }}
        />
      )}

      {showHistory && form.id && (
        <VersionHistoryModal
          workflowId={form.id}
          currentVersion={selectedWorkflow?.version ?? 0}
          locked={authoringLocked}
          onClose={() => setShowHistory(false)}
          onRestored={async (workflow) => {
            setFormBaselined(toForm(workflow));
            setShowHistory(false);
            await loadList();
          }}
        />
      )}

      {showImport && (
        <ImportWorkflowsModal
          locked={authoringLocked}
          onClose={() => setShowImport(false)}
          onImported={() => loadList()}
        />
      )}

      <AssistantShelf
        open={showAssistant}
        onOpen={() => setShowAssistant(true)}
        onClose={() => setShowAssistant(false)}
        // Keep the shelf open while co-authoring — clicking into the editor on
        // the left shouldn't collapse it. We collapse it ourselves when the user
        // leaves the studio (see the effect above); leaving the page entirely
        // unmounts it.
        closeOnClickOutside={false}
        title="Authoring assistant"
        widthStorageKey="authoring_assistant_width"
        subtitle="Ask me to explain a field, or to draft / edit this workflow. I can see what's open in the editor — including your unsaved edits — so ask for changes on top of your own wording. When I save a draft, it opens automatically on the left."
        headerActions={
          <button
            type="button"
            onClick={() => loadList()}
            title="Reload the workflows list (after the assistant saves changes)"
            className="text-gray-400 hover:text-accent p-1"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
        }
      >
        <ChatView
          mode="authoring"
          storageKey="chatview_messages_authoring"
          onToolResult={handleAuthoringToolResult}
          extraContext={editorDraftContext}
          placeholder="Ask about authoring workflows..."
          welcomeNode={
            <div className="text-center px-2 pt-2">
              <Sparkles className="w-6 h-6 text-accent mx-auto mb-2" />
              <p className="text-sm font-medium text-heading">Authoring assistant</p>
              <p className="text-xs text-gray-500 mt-1">
                I can explain how workflows work and help you build or edit one.
              </p>
            </div>
          }
          samplePrompts={[
            'How do I add an approval gate before a provisioning step?',
            'What tools can a workflow step call?',
            'Draft a workflow that grants table access after manager approval.',
            'Explain the auto-approve condition options.',
          ]}
        />
      </AssistantShelf>
    </div>
  );
}

export default Workflows;
