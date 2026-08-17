/**
 * `WorkflowTestsPanel` — the Tests tab of Workflow Studio.
 *
 * A case is a question plus, in plain English, what should happen. Running one
 * starts the *real* agent against this workflow's instructions and tools with every
 * mutating tool sandboxed, then an LLM judge compares the transcript to the
 * expectation. Because the judge is non-deterministic, a verdict is never shown on
 * its own: the rationale and the full transcript are always one click away, and any
 * case can be re-run.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  AlertCircle,
  Check,
  ChevronDown,
  ChevronRight,
  FlaskConical,
  Loader2,
  Play,
  Plus,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react';
import { Button } from '../ui/button';
import { api } from '../../services/api';
import type {
  WorkflowTest,
  WorkflowTestHealth,
  WorkflowTestRun,
} from '../../services/api';
import { HelpTip, LabelWithHelp, AskAgentHint } from '../ui/help-tip';

const inputClass =
  'w-full border border-gray-300 rounded-md px-2.5 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-accent';
const labelClass = 'block text-[11px] font-medium text-gray-600 mb-1';

// The run group is polled rather than streamed: each case is a full agent
// conversation, so results arrive over tens of seconds.
const POLL_MS = 2500;

interface Props {
  workflowId: string | null;
  /** True while the open workflow has unsaved edits — tests run against the
   *  SAVED workflow, so we have to say so rather than imply otherwise. */
  dirty?: boolean;
  /** Changes when the authoring assistant writes cases, so its proposals appear
   *  here without the admin having to leave and come back. */
  reloadToken?: number;
  onAskAgent?: () => void;
}

export default function WorkflowTestsPanel({
  workflowId,
  dirty,
  reloadToken,
  onAskAgent,
}: Props) {
  const [tests, setTests] = useState<WorkflowTest[]>([]);
  const [health, setHealth] = useState<WorkflowTestHealth | null>(null);
  const [enabled, setEnabled] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<'generate' | 'run' | null>(null);
  const [runs, setRuns] = useState<Record<string, WorkflowTestRun>>({});
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [draft, setDraft] = useState<{ name: string; question: string; expected: string } | null>(
    null,
  );
  const pollTimer = useRef<number | null>(null);

  const load = useCallback(async () => {
    if (!workflowId) return;
    setLoading(true);
    setError(null);
    try {
      const data = await api.listWorkflowTests(workflowId);
      setTests(data.tests || []);
      setHealth(data.health || null);
      setEnabled(data.enabled !== false);
      // Seed the run map from each case's latest run so a reload doesn't look
      // like nothing was ever tested.
      const seeded: Record<string, WorkflowTestRun> = {};
      for (const t of data.tests || []) {
        if (t.latest_run) seeded[t.id] = t.latest_run;
      }
      setRuns(seeded);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load tests');
    } finally {
      setLoading(false);
    }
  }, [workflowId]);

  useEffect(() => {
    void load();
  }, [load, reloadToken]);

  // Stop polling when the panel unmounts (e.g. tab switch) so a long run can't
  // keep firing requests against a closed workflow.
  useEffect(() => {
    return () => {
      if (pollTimer.current) window.clearTimeout(pollTimer.current);
    };
  }, []);

  const pollGroup = useCallback(
    async (groupId: string) => {
      if (!workflowId) return;
      try {
        const group = await api.getWorkflowTestRunGroup(workflowId, groupId);
        setRuns((prev) => {
          const next = { ...prev };
          for (const run of group.runs) next[run.test_id] = run;
          return next;
        });
        if (group.health) setHealth(group.health);
        if (!group.done) {
          pollTimer.current = window.setTimeout(() => void pollGroup(groupId), POLL_MS);
        } else {
          setBusy(null);
        }
      } catch (e) {
        setBusy(null);
        setError(e instanceof Error ? e.message : 'Lost track of the test run');
      }
    },
    [workflowId],
  );

  const run = async (testIds?: string[]) => {
    if (!workflowId) return;
    setError(null);
    setNotice(null);
    setBusy('run');
    try {
      const { run_group_id, runs: started } = await api.runWorkflowTests(workflowId, testIds);
      setRuns((prev) => {
        const next = { ...prev };
        for (const r of started) next[r.test_id] = r;
        return next;
      });
      // Expand what we just launched: the point of running is to read the result.
      setExpanded((prev) => {
        const next = { ...prev };
        for (const r of started) next[r.test_id] = true;
        return next;
      });
      void pollGroup(run_group_id);
    } catch (e) {
      setBusy(null);
      setError(e instanceof Error ? e.message : 'Failed to start the test run');
    }
  };

  const propose = async () => {
    if (!workflowId) return;
    setError(null);
    setBusy('generate');
    try {
      const result = await api.generateWorkflowTests(workflowId, { count: 5, save: true });
      setNotice(
        result.warning ||
          `Proposed ${result.cases.length} case${result.cases.length === 1 ? '' : 's'} — ` +
            'review and edit them, then run.',
      );
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to propose cases');
    } finally {
      setBusy(null);
    }
  };

  const saveDraft = async () => {
    if (!workflowId || !draft) return;
    if (!draft.question.trim() || !draft.expected.trim()) {
      setError('A case needs both a question and an expected outcome.');
      return;
    }
    setError(null);
    try {
      await api.createWorkflowTest(workflowId, {
        name: draft.name,
        question: draft.question,
        expected_outcome: draft.expected,
      });
      setDraft(null);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save the case');
    }
  };

  const patch = async (test: WorkflowTest, changes: Partial<WorkflowTest>) => {
    if (!workflowId) return;
    setTests((prev) => prev.map((t) => (t.id === test.id ? { ...t, ...changes } : t)));
    try {
      await api.updateWorkflowTest(workflowId, test.id, {
        name: changes.name,
        question: changes.question,
        expected_outcome: changes.expected_outcome,
        enabled: changes.enabled,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save the case');
      await load();
    }
  };

  const remove = async (test: WorkflowTest) => {
    if (!workflowId) return;
    try {
      await api.deleteWorkflowTest(workflowId, test.id);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to delete the case');
    }
  };

  if (!workflowId) {
    return (
      <div className="text-sm text-gray-500 border border-dashed border-gray-200 rounded-md p-6 text-center">
        Save this workflow as a draft first — tests run the agent against a saved
        workflow's instructions and tools.
      </div>
    );
  }

  const running = busy === 'run';

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 text-sm font-semibold text-gray-700">
            <FlaskConical className="w-4 h-4 text-accent" /> Behavioral tests
            <HelpTip text="Each case runs the real agent against this workflow's instructions and allowed tools, with every mutating tool sandboxed — nothing is provisioned. An LLM judge then compares what the agent did to your expected outcome. Tests run against the SAVED workflow, so save your edits first." />
          </div>
          <p className="text-xs text-gray-500 mt-1 max-w-2xl">
            Ask what a real user would ask, then describe what should happen in plain
            English. Describe behavior — what it asks for, what it refuses, which tool
            it calls — not exact wording.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <AskAgentHint onClick={onAskAgent} label="Ask the agent" />
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={propose}
            disabled={busy !== null}
          >
            {busy === 'generate' ? (
              <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" />
            ) : (
              <Sparkles className="w-3.5 h-3.5 mr-1" />
            )}
            Propose cases
          </Button>
          <Button
            type="button"
            size="sm"
            onClick={() => void run()}
            disabled={busy !== null || tests.filter((t) => t.enabled).length === 0 || !enabled}
          >
            {running ? (
              <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" />
            ) : (
              <Play className="w-3.5 h-3.5 mr-1" />
            )}
            Run all
          </Button>
        </div>
      </div>

      {!enabled && (
        <div className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded p-2">
          Test runs are turned off in this environment (Admin → Settings → Workflow
          tests). You can still author cases.
        </div>
      )}

      {dirty && (
        <div className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded p-2">
          You have unsaved changes. Tests run against the last saved version of this
          workflow, so save before running to test what you're looking at.
        </div>
      )}

      {health && health.total > 0 && (
        <div className="flex items-center gap-3 text-xs text-gray-600 flex-wrap">
          <HealthChip
            label={`${health.passing} passing`}
            tone={health.passing === health.total ? 'good' : 'neutral'}
          />
          {health.failing > 0 && <HealthChip label={`${health.failing} failing`} tone="bad" />}
          {health.errored > 0 && <HealthChip label={`${health.errored} errored`} tone="bad" />}
          {health.never_run > 0 && (
            <HealthChip label={`${health.never_run} never run`} tone="warn" />
          )}
          {health.stale > 0 && <HealthChip label={`${health.stale} stale`} tone="warn" />}
          <span className="text-gray-400">pass threshold {health.pass_threshold}</span>
        </div>
      )}

      {error && (
        <div className="text-xs text-red-600 bg-red-50 border border-red-200 rounded p-2 flex items-start gap-1.5">
          <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}
      {notice && (
        <div className="text-xs text-gray-600 bg-gray-50 border border-gray-200 rounded p-2">
          {notice}
        </div>
      )}

      {loading && tests.length === 0 ? (
        <div className="text-sm text-gray-400 flex items-center gap-2 p-4">
          <Loader2 className="w-4 h-4 animate-spin" /> Loading cases…
        </div>
      ) : tests.length === 0 && !draft ? (
        <div className="border border-dashed border-gray-200 rounded-md p-6 text-center space-y-3">
          <p className="text-sm text-gray-500">
            No test cases yet. Nothing is verifying that this workflow behaves the way
            you expect.
          </p>
          <div className="flex items-center justify-center gap-2">
            <Button type="button" size="sm" onClick={propose} disabled={busy !== null}>
              {busy === 'generate' ? (
                <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" />
              ) : (
                <Sparkles className="w-3.5 h-3.5 mr-1" />
              )}
              Propose 5 cases
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setDraft({ name: '', question: '', expected: '' })}
            >
              <Plus className="w-3.5 h-3.5 mr-1" /> Write one myself
            </Button>
          </div>
        </div>
      ) : (
        <div className="space-y-2">
          {tests.map((test) => (
            <TestCaseRow
              key={test.id}
              test={test}
              run={runs[test.id]}
              expanded={!!expanded[test.id]}
              busy={busy !== null}
              onToggleExpanded={() =>
                setExpanded((prev) => ({ ...prev, [test.id]: !prev[test.id] }))
              }
              onRun={() => void run([test.id])}
              onPatch={(changes) => void patch(test, changes)}
              onRemove={() => void remove(test)}
            />
          ))}
        </div>
      )}

      {draft ? (
        <div className="border border-accent/40 rounded-md p-3 space-y-3 bg-accent/5">
          <div>
            <LabelWithHelp className={labelClass} help="Short label, e.g. 'missing catalog name'.">
              Case name
            </LabelWithHelp>
            <input
              className={inputClass}
              value={draft.name}
              placeholder="e.g. happy path"
              onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            />
          </div>
          <div>
            <LabelWithHelp
              className={labelClass}
              help="Exactly what the user types to the agent. Use realistic values."
            >
              Question the user asks
            </LabelWithHelp>
            <textarea
              className={inputClass}
              rows={2}
              value={draft.question}
              placeholder="e.g. I need read access to platform_catalog.sales.orders for the Q3 forecast"
              onChange={(e) => setDraft({ ...draft, question: e.target.value })}
            />
          </div>
          <div>
            <LabelWithHelp
              className={labelClass}
              help="What should happen, in plain English. Make it checkable from a transcript: 'asks for the business justification and does not call grant_uc_access yet' is good; 'handles it correctly' is not."
            >
              Expected outcome
            </LabelWithHelp>
            <textarea
              className={inputClass}
              rows={3}
              value={draft.expected}
              placeholder="e.g. Confirms the table exists, asks for a business justification, then submits the request and tells the user it's pending data-owner approval."
              onChange={(e) => setDraft({ ...draft, expected: e.target.value })}
            />
          </div>
          <div className="flex items-center gap-2">
            <Button type="button" size="sm" onClick={saveDraft}>
              Add case
            </Button>
            <Button type="button" variant="ghost" size="sm" onClick={() => setDraft(null)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        tests.length > 0 && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setDraft({ name: '', question: '', expected: '' })}
          >
            <Plus className="w-3.5 h-3.5 mr-1" /> Add case
          </Button>
        )
      )}
    </div>
  );
}

function HealthChip({ label, tone }: { label: string; tone: 'good' | 'bad' | 'warn' | 'neutral' }) {
  const cls =
    tone === 'good'
      ? 'bg-green-50 text-green-700 border-green-200'
      : tone === 'bad'
        ? 'bg-red-50 text-red-700 border-red-200'
        : tone === 'warn'
          ? 'bg-amber-50 text-amber-700 border-amber-200'
          : 'bg-gray-50 text-gray-600 border-gray-200';
  return <span className={`rounded-full border px-2 py-0.5 ${cls}`}>{label}</span>;
}

function VerdictChip({ run }: { run?: WorkflowTestRun }) {
  if (!run) {
    return (
      <span className="rounded-full border border-gray-200 bg-gray-50 text-gray-500 text-[11px] px-2 py-0.5">
        never run
      </span>
    );
  }
  if (run.status === 'queued' || run.status === 'running') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full border border-blue-200 bg-blue-50 text-blue-700 text-[11px] px-2 py-0.5">
        <Loader2 className="w-3 h-3 animate-spin" />
        {run.status === 'queued' ? 'queued' : 'running'}
      </span>
    );
  }
  if (run.status === 'error') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full border border-red-200 bg-red-50 text-red-700 text-[11px] px-2 py-0.5">
        <AlertCircle className="w-3 h-3" /> error
      </span>
    );
  }
  const passed = run.passed;
  const label = run.verdict === 'partial' && !passed ? 'partial' : passed ? 'pass' : 'fail';
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border text-[11px] px-2 py-0.5 ${
        passed
          ? 'border-green-200 bg-green-50 text-green-700'
          : 'border-red-200 bg-red-50 text-red-700'
      }`}
    >
      {passed ? <Check className="w-3 h-3" /> : <X className="w-3 h-3" />}
      {label}
      {typeof run.score === 'number' && <span className="opacity-70">{run.score}</span>}
    </span>
  );
}

function TestCaseRow({
  test,
  run,
  expanded,
  busy,
  onToggleExpanded,
  onRun,
  onPatch,
  onRemove,
}: {
  test: WorkflowTest;
  run?: WorkflowTestRun;
  expanded: boolean;
  busy: boolean;
  onToggleExpanded: () => void;
  onRun: () => void;
  onPatch: (changes: Partial<WorkflowTest>) => void;
  onRemove: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(test.name);
  const [question, setQuestion] = useState(test.question);
  const [expected, setExpected] = useState(test.expected_outcome);

  useEffect(() => {
    setName(test.name);
    setQuestion(test.question);
    setExpected(test.expected_outcome);
  }, [test.name, test.question, test.expected_outcome]);

  const commit = () => {
    setEditing(false);
    if (
      name === test.name &&
      question === test.question &&
      expected === test.expected_outcome
    ) {
      return;
    }
    onPatch({ name, question, expected_outcome: expected });
  };

  return (
    <div className={`border rounded-md ${test.enabled ? 'border-gray-200' : 'border-gray-200 bg-gray-50/60'}`}>
      <div className="flex items-center gap-2 px-3 py-2">
        <button
          type="button"
          onClick={onToggleExpanded}
          className="text-gray-400 hover:text-gray-600 shrink-0"
          aria-label={expanded ? 'Collapse' : 'Expand'}
        >
          {expanded ? (
            <ChevronDown className="w-4 h-4" />
          ) : (
            <ChevronRight className="w-4 h-4" />
          )}
        </button>
        <span className="text-sm font-medium text-gray-800 truncate flex-1">
          {test.name || '(unnamed case)'}
          {test.source === 'agent' && (
            <span className="ml-2 text-[10px] text-gray-400 font-normal">proposed</span>
          )}
        </span>
        <VerdictChip run={run} />
        <label className="flex items-center gap-1 text-[11px] text-gray-500">
          <input
            type="checkbox"
            checked={test.enabled}
            onChange={(e) => onPatch({ enabled: e.target.checked })}
          />
          enabled
        </label>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onRun}
          disabled={busy}
          title="Run just this case"
        >
          <Play className="w-3.5 h-3.5" />
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onRemove}
          title="Delete this case"
        >
          <Trash2 className="w-3.5 h-3.5 text-gray-400" />
        </Button>
      </div>

      {expanded && (
        <div className="border-t border-gray-100 px-3 py-3 space-y-3">
          {editing ? (
            <>
              <div>
                <label className={labelClass}>Case name</label>
                <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)} />
              </div>
              <div>
                <label className={labelClass}>Question the user asks</label>
                <textarea
                  className={inputClass}
                  rows={2}
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                />
              </div>
              <div>
                <label className={labelClass}>Expected outcome</label>
                <textarea
                  className={inputClass}
                  rows={3}
                  value={expected}
                  onChange={(e) => setExpected(e.target.value)}
                />
              </div>
              <div className="flex items-center gap-2">
                <Button type="button" size="sm" onClick={commit}>
                  Save case
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setName(test.name);
                    setQuestion(test.question);
                    setExpected(test.expected_outcome);
                    setEditing(false);
                  }}
                >
                  Cancel
                </Button>
              </div>
            </>
          ) : (
            <>
              <div>
                <p className="text-[11px] uppercase tracking-wider text-gray-400 font-bold mb-1">
                  Question
                </p>
                <p className="text-sm text-gray-700 whitespace-pre-wrap">{test.question}</p>
              </div>
              <div>
                <p className="text-[11px] uppercase tracking-wider text-gray-400 font-bold mb-1">
                  Expected outcome
                </p>
                <p className="text-sm text-gray-700 whitespace-pre-wrap">
                  {test.expected_outcome}
                </p>
              </div>
              <Button type="button" variant="outline" size="sm" onClick={() => setEditing(true)}>
                Edit case
              </Button>
            </>
          )}

          {run && <RunResult run={run} />}
        </div>
      )}
    </div>
  );
}

function RunResult({ run }: { run: WorkflowTestRun }) {
  const [showTranscript, setShowTranscript] = useState(false);

  if (run.status === 'queued' || run.status === 'running') {
    return (
      <div className="text-xs text-blue-700 bg-blue-50 border border-blue-200 rounded p-2 flex items-center gap-2">
        <Loader2 className="w-3.5 h-3.5 animate-spin" />
        Running the agent against this case…
      </div>
    );
  }

  if (run.status === 'error') {
    return (
      <div className="text-xs text-red-700 bg-red-50 border border-red-200 rounded p-2">
        <p className="font-semibold mb-1">This case couldn't be scored</p>
        <p>{run.error || 'Unknown error.'}</p>
      </div>
    );
  }

  return (
    <div className="border border-gray-200 rounded p-2.5 space-y-2 bg-gray-50/60">
      <div className="flex items-center gap-2">
        <VerdictChip run={run} />
        {typeof run.duration_ms === 'number' && (
          <span className="text-[11px] text-gray-400">
            {(run.duration_ms / 1000).toFixed(1)}s
          </span>
        )}
        {run.completed_at && (
          <span className="text-[11px] text-gray-400">
            {new Date(run.completed_at).toLocaleString()}
          </span>
        )}
      </div>

      {run.rationale && (
        <div>
          <p className="text-[11px] uppercase tracking-wider text-gray-400 font-bold mb-1">
            Why the judge said that
          </p>
          <p className="text-xs text-gray-700 whitespace-pre-wrap">{run.rationale}</p>
        </div>
      )}

      {run.missing && run.missing.length > 0 && (
        <div>
          <p className="text-[11px] uppercase tracking-wider text-gray-400 font-bold mb-1">
            Expectations not met
          </p>
          <ul className="list-disc pl-4 text-xs text-gray-700 space-y-0.5">
            {run.missing.map((m, i) => (
              <li key={i}>{m}</li>
            ))}
          </ul>
        </div>
      )}

      {run.tool_calls && run.tool_calls.length > 0 && (
        <div>
          <p className="text-[11px] uppercase tracking-wider text-gray-400 font-bold mb-1">
            Tools the agent called (mutating calls were simulated)
          </p>
          <div className="flex flex-wrap gap-1.5">
            {run.tool_calls.map((call, i) => (
              <span
                key={i}
                className="inline-flex items-center gap-1 rounded-full border border-gray-200 bg-white px-2 py-0.5 text-[11px] text-gray-700 font-mono"
                title={JSON.stringify(call.arguments ?? {}, null, 2)}
              >
                {call.name}
              </span>
            ))}
          </div>
        </div>
      )}

      {run.transcript && run.transcript.length > 0 && (
        <div>
          <button
            type="button"
            className="text-[11px] text-accent hover:underline inline-flex items-center gap-1"
            onClick={() => setShowTranscript((v) => !v)}
          >
            {showTranscript ? (
              <ChevronDown className="w-3 h-3" />
            ) : (
              <ChevronRight className="w-3 h-3" />
            )}
            {showTranscript ? 'Hide' : 'Show'} transcript ({run.transcript.length} messages)
          </button>
          {showTranscript && (
            <div className="mt-2 space-y-2 max-h-80 overflow-y-auto">
              {run.transcript.map((msg, i) => (
                <div key={i} className="text-xs">
                  <p className="text-[10px] uppercase tracking-wider text-gray-400 font-bold">
                    {msg.role}
                    {msg.name ? ` · ${msg.name}` : ''}
                  </p>
                  {msg.content && (
                    <p className="text-gray-700 whitespace-pre-wrap break-words">
                      {msg.content}
                    </p>
                  )}
                  {msg.tool_calls && msg.tool_calls.length > 0 && (
                    <p className="text-gray-500 font-mono">
                      called: {msg.tool_calls.map((c) => c.name).join(', ')}
                    </p>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
