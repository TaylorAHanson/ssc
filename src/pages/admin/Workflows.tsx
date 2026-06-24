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
} from 'lucide-react';
import { api } from '../../services/api';
import type { Workflow, WorkflowInput, WorkflowGraphSpec, WorkflowTool } from '../../services/api';
import { WorkflowEditor } from '../../components/admin/WorkflowEditor';
import { PublishConfirmModal } from '../../components/admin/PublishConfirmModal';
import { VersionHistoryModal } from '../../components/admin/VersionHistoryModal';
import { ImportWorkflowsModal } from '../../components/admin/ImportWorkflowsModal';
import { useBrandingStore } from '../../stores/brandingStore';
import { Lock, Sparkles, RefreshCw } from 'lucide-react';
import { LabelWithHelp, AskAgentHint } from '../../components/ui/help-tip';
import { ChatView } from '../../components/chat/ChatView';
import { AssistantShelf } from '../../components/assistant/AssistantShelf';

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
};

// "Compound" = the spec composes another workflow via a subworkflow ("Call
// workflow") stage; otherwise it's "atomic". Mirrors the backend derivation so
// the editor can badge an unsaved spec before the server round-trip.
function specComposition(spec: WorkflowGraphSpec | null | undefined): 'atomic' | 'compound' {
  const stages = spec?.stages ?? [];
  return stages.some((s) => s.kind === 'subworkflow') ? 'compound' : 'atomic';
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
  const tab: 'details' | 'workflow' =
    searchParams.get('tab') === 'workflow' ? 'workflow' : 'details';
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

  const handleAuthoringToolResult = async (
    toolName: string,
    result: unknown,
    ok: boolean,
    args?: Record<string, unknown>,
  ) => {
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
    const spec = (typeof rawSpec === 'string'
      ? safeParseJson(rawSpec)
      : rawSpec) as WorkflowGraphSpec | null;
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
      setForm((prev) => {
        const targetKey = argKey || specName || prev.key;
        // If the agent is drafting a *different* workflow than what's open,
        // start clean so we don't inherit the open record's id (which would
        // make a manual Save update the wrong workflow).
        const base = prev.key && targetKey && targetKey !== prev.key ? emptyForm : prev;
        return {
          ...base,
          key: base.key || targetKey,
          name: base.name || argName || targetKey,
          // Default request_type to the explicit arg, else the spec name, so a
          // manual Save during the design phase doesn't persist a blank type.
          request_type: base.request_type || argRt || specName,
          goal: base.goal || argGoal,
          graph_spec: spec,
        };
      });
      // Show the editor full-page so the admin watches the design take shape.
      // Before a save there's no id, so flag `new=1` to keep us in edit view.
      setSearchParams(
        (prev) => {
          const params = new URLSearchParams(prev);
          params.set('tab', 'workflow');
          if (!params.get('workflow')) params.set('new', '1');
          return params;
        },
        { replace: true },
      );
    }

    // 2) On a successful persist, open the editor for the saved workflow and
    //    reload the canonical record. The result payload is normally an object
    //    but parse a stringified one defensively so the open never silently skips.
    const r = (typeof result === 'string'
      ? safeParseJson(result)
      : result) as { ok?: boolean; key?: string } | null;
    const persisted = toolName === 'save_workflow_draft' || toolName === 'publish_workflow';
    if (persisted && ok && r?.ok && r.key) {
      const savedKey = r.key;
      // Switch to the editor view immediately — before the (heavier) reload — so
      // a refetch hiccup or a key→id lookup miss can't strand the admin on the
      // list. "Save a draft" should always land you on that draft's page.
      setTab('workflow');
      // Keep the editor open on the just-hydrated draft as a floor; the canonical
      // id (set below) replaces `new=1` once we resolve it.
      const keepEditorOpen = () =>
        setSearchParams(
          (prev) => {
            const params = new URLSearchParams(prev);
            if (!params.get('workflow')) params.set('new', '1');
            return params;
          },
          { replace: true },
        );
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
            setBaseline(JSON.stringify(nextForm));
            return nextForm;
          });
          setWorkflowParam(match.id);
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

  const setFormBaselined = (next: WorkflowFormState) => {
    setForm(next);
    setBaseline(JSON.stringify(next));
  };

  const setTab = (next: 'details' | 'workflow') => {
    setSearchParams(
      (prev) => {
        const params = new URLSearchParams(prev);
        params.set('tab', next);
        return params;
      },
      { replace: true },
    );
  };

  const setWorkflowParam = (id: string | null, opts: { replace?: boolean } = {}) => {
    setSearchParams(
      (prev) => {
        const params = new URLSearchParams(prev);
        if (id) {
          params.set('workflow', id);
          params.delete('new');
        } else {
          params.delete('workflow');
        }
        return params;
      },
      opts,
    );
  };

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
    // Guard unsaved edits before swapping workflows (covers Back/Forward too).
    if (!confirmDiscard()) {
      setWorkflowParam(loadedIdRef.current, { replace: true });
      return;
    }
    if (!workflowParam) {
      loadedIdRef.current = null;
      setFormBaselined(emptyForm);
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

  const buildInput = (): WorkflowInput => ({
    key: form.key.trim(),
    name: form.name.trim() || form.key.trim(),
    goal: form.goal.trim() || null,
    instructions_markdown: form.instructions_markdown,
    allowed_tools: form.allowed_tools.trim() ? splitList(form.allowed_tools) : null,
    policy_ref: form.policy_ref.trim() || null,
    request_type: form.request_type.trim() || null,
    status: form.status,
    graph_spec: form.graph_spec,
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
      setError(e instanceof Error ? e.message : 'Failed to save workflow');
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
            <Button onClick={startNew} variant="outline">
              <Plus className="w-4 h-4 mr-1" /> New workflow
            </Button>
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
            bundle via <span className="font-medium">Import</span>.
          </div>
        </div>
      )}

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 rounded-md px-4 py-2 text-sm">
          {error}
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
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
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
                    {s.status === 'published' ? (
                      <span className="inline-flex items-center gap-1 text-[11px] text-green-700">
                        <CheckCircle2 className="w-3 h-3" /> live
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 text-[11px] text-gray-400">
                        <Circle className="w-3 h-3" /> draft
                      </span>
                    )}
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
                  onChange={(e) => setForm({ ...form, key: e.target.value })}
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
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
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
                onChange={(e) => setForm({ ...form, goal: e.target.value })}
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
                  onChange={(e) => setForm({ ...form, policy_ref: e.target.value })}
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
                  onChange={(e) => setForm({ ...form, request_type: e.target.value })}
                />
              </div>
              <div>
                <LabelWithHelp
                  className="text-xs font-medium text-gray-600 mb-1"
                  help="Draft = saved but not live; safe to edit and dry-run. Published = live and governing real requests. Prefer the Publish button so the pre-publish checks run."
                >
                  Status
                </LabelWithHelp>
                <select
                  className={inputClass}
                  value={form.status}
                  disabled={authoringLocked}
                  onChange={(e) => setForm({ ...form, status: e.target.value })}
                >
                  <option value="draft">draft</option>
                  <option value="published">published</option>
                </select>
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
                onChange={(e) => setForm({ ...form, allowed_tools: e.target.value })}
              />
            </div>

            <div>
              <LabelWithHelp
                className="text-xs font-medium text-gray-600 mb-1"
                help="Markdown instructions the agent follows when running this workflow: the goal, what information to gather, and how to behave. This is the prompt guidance, separate from the executable workflow graph."
              >
                Instructions (markdown)
              </LabelWithHelp>
              <textarea
                className={textareaClass}
                rows={16}
                placeholder="# Workflow Instructions&#10;&#10;**Goal**: ...&#10;&#10;## Information to Gather&#10;..."
                value={form.instructions_markdown}
                disabled={authoringLocked}
                onChange={(e) =>
                  setForm({ ...form, instructions_markdown: e.target.value })
                }
              />
            </div>
            </>
            )}

            {tab === 'workflow' && (
              <div>
                {form.graph_spec ? (
                  <WorkflowEditor
                    spec={form.graph_spec}
                    tools={tools}
                    onChange={(graph_spec) => setForm({ ...form, graph_spec })}
                    onAskAgent={() => setShowAssistant(true)}
                  />
                ) : (
                  <div className="text-center py-10 border border-dashed border-gray-200 rounded-lg">
                    <WorkflowIcon className="w-8 h-8 text-gray-300 mx-auto mb-3" />
                    <p className="text-sm text-gray-600 mb-1">No workflow graph yet</p>
                    <p className="text-xs text-gray-400 max-w-md mx-auto mb-4">
                      Build an executable workflow as ordered approval gates and provisioning
                      steps. When published, the durable executor runs this graph instead of the
                      code catalog.
                    </p>
                    <Button
                      variant="outline"
                      onClick={() =>
                        setForm({
                          ...form,
                          graph_spec: {
                            name: form.key.trim() || form.request_type.trim() || 'workflow',
                            completed_status: 'completed',
                            complete_fact: null,
                            stages: [],
                          },
                        })
                      }
                    >
                      <Plus className="w-4 h-4 mr-1" /> Start a workflow
                    </Button>
                  </div>
                )}
              </div>
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
        title="Authoring assistant"
        widthStorageKey="authoring_assistant_width"
        subtitle="Ask me to explain a field, or to draft / edit this workflow. When I save a draft, it opens automatically in the editor on the left."
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
