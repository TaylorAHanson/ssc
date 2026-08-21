import { useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  Compass,
  FlaskConical,
  GitBranch,
  Loader2,
  Send,
  ShieldAlert,
  Sparkles,
  Wrench,
  X,
} from 'lucide-react';
import { Button } from '../ui/button';
import { api } from '../../services/api';
import type {
  GoalQuality,
  InstructionsQuality,
  Workflow,
  WorkflowGraphSpec,
  WorkflowTestHealth,
  WorkflowTool,
} from '../../services/api';

interface Props {
  workflow: Workflow;
  graphSpec: WorkflowGraphSpec | null;
  tools: WorkflowTool[];
  onConfirm: () => Promise<void>;
  onClose: () => void;
}

/** Confirmation shown before a draft workflow goes live. Validates the graph (if any)
 *  and summarizes the "blast radius" so a platform admin sees exactly what they're
 *  turning on: gates, steps, mutating actions, and the request-type binding. */
export function PublishConfirmModal({ workflow, graphSpec, tools, onConfirm, onClose }: Props) {
  const [validating, setValidating] = useState(!!graphSpec);
  const [valid, setValid] = useState(!graphSpec);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [publishing, setPublishing] = useState(false);
  // Structural validity says the graph compiles; these two say whether the thing
  // an admin is about to make live has an authored playbook and passing tests.
  // Publishing "just the graph" is how a workflow ships with a generated stub.
  const [tests, setTests] = useState<
    { health: WorkflowTestHealth; blocksPublish: boolean } | null
  >(null);
  // Three states, not two: an unresolved playbook must not render as 0/100. Reading
  // "no instructions" off a workflow summary that simply omits the field is what
  // made every authored playbook look like a stub here.
  const [instructions, setInstructions] = useState<
    | { status: 'loading' }
    | { status: 'unavailable' }
    | { status: 'scored'; quality: InstructionsQuality }
  >({ status: 'loading' });
  // The goal is the workflow's whole line in the runtime capabilities menu, so a
  // stub or a line that reads like another workflow's is a routing bug — worth
  // catching at the moment it goes live rather than after a misrouted request.
  const [goalQuality, setGoalQuality] = useState<GoalQuality | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .listWorkflowTests(workflow.id)
      .then((data) => {
        if (cancelled) return;
        setTests({ health: data.health, blocksPublish: data.blocks_publish });
      })
      .catch(() => {
        // Advisory only: a failed lookup must not stop a publish.
        if (!cancelled) setTests(null);
      });
    return () => {
      cancelled = true;
    };
  }, [workflow.id]);

  useEffect(() => {
    let cancelled = false;
    setInstructions({ status: 'loading' });
    (async () => {
      // The list API serves summaries with the playbook stripped, so the workflow
      // handed to this modal usually carries no `instructions_markdown` at all.
      // Fetch the server's copy by id — that is the text Publish makes live, and
      // it's the same by-id lookup the tests and goal panels already do.
      let markdown =
        workflow.instructions_markdown === undefined
          ? undefined
          : workflow.instructions_markdown ?? '';
      if (markdown === undefined) {
        try {
          markdown = (await api.getWorkflow(workflow.id)).instructions_markdown ?? '';
        } catch {
          // Stays undefined so the backend skips scoring: unknown is not empty,
          // and empty legitimately scores 0 with a "ships a stub" warning.
          markdown = undefined;
        }
      }
      if (cancelled) return;
      try {
        const report = await api.evaluateSpec(
          graphSpec || { name: workflow.key, stages: [] },
          markdown,
          { goal: workflow.goal ?? '', key: workflow.key },
        );
        if (cancelled) return;
        setGoalQuality(report.goal ?? null);
        setInstructions(
          report.instructions
            ? { status: 'scored', quality: report.instructions }
            : { status: 'unavailable' },
        );
      } catch {
        if (cancelled) return;
        setGoalQuality(null);
        setInstructions({ status: 'unavailable' });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [workflow.id, workflow.key, workflow.goal, workflow.instructions_markdown, graphSpec]);

  useEffect(() => {
    let cancelled = false;
    if (!graphSpec) return;
    setValidating(true);
    api
      .validateSpec(graphSpec)
      .then(() => {
        if (cancelled) return;
        setValid(true);
        setValidationError(null);
      })
      .catch((e) => {
        if (cancelled) return;
        setValid(false);
        setValidationError(e instanceof Error ? e.message : 'Invalid workflow');
      })
      .finally(() => !cancelled && setValidating(false));
    return () => {
      cancelled = true;
    };
  }, [graphSpec]);

  const summary = useMemo(() => {
    const stages = graphSpec?.stages || [];
    const gates = stages.filter((s) => s.kind === 'gate');
    const steps = stages.filter((s) => s.kind === 'step');
    const mutating = steps.filter((s) => {
      const t = tools.find((tt) => tt.name === (s as { tool?: string }).tool);
      return t?.is_mutating;
    });
    const approvers = Array.from(new Set(gates.map((g) => (g as { type?: string }).type))).filter(Boolean);
    const externalTools = steps.filter((s) => {
      const t = tools.find((tt) => tt.name === (s as { tool?: string }).tool);
      return t?.external;
    });
    return { gates, steps, mutating, approvers, externalTools };
  }, [graphSpec, tools]);

  const confirm = async () => {
    setPublishing(true);
    try {
      await onConfirm();
    } finally {
      setPublishing(false);
    }
  };

  const hasGraph = !!graphSpec && (graphSpec.stages?.length ?? 0) > 0;
  const noRequestType = hasGraph && !workflow.request_type;

  // A single "close to the generated baseline" line misdiagnosed every low score.
  // The scorer already distinguishes the cases an author actually hits, so say
  // which one this is. An untouched baseline warns at any score: the baseline
  // penalty is only 35, so a tidily-generated stub clears a score threshold while
  // still being the thing this dialog exists to catch.
  const instructionsWarning =
    instructions.status !== 'scored'
      ? null
      : instructions.quality.summary.chars === 0
        ? 'This workflow has no runtime instructions, so the agent has nothing to follow when a request comes in.'
        : instructions.quality.summary.is_auto_baseline
          ? 'These instructions are still the generated baseline, which only covers what the graph happens to reference.'
          : instructions.quality.score < 40
            ? `These instructions scored ${instructions.quality.score}/100 — thin for something the agent runs a governed request from.`
            : null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg flex flex-col">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200">
          <div className="flex items-center gap-2">
            <Send className="w-4 h-4 text-accent" />
            <h2 className="text-sm font-semibold">Publish “{workflow.key}”?</h2>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-5 space-y-4">
          <p className="text-sm text-gray-600">
            Publishing makes this the <span className="font-medium">live</span> version
            {workflow.request_type ? (
              <> for request type <code className="text-xs">{workflow.request_type}</code></>
            ) : null}
            . It becomes v{workflow.version + 1} and the agent and durable executor will use it immediately.
          </p>

          {hasGraph ? (
            <div className="border border-gray-200 rounded-lg divide-y divide-gray-100 text-sm">
              <div className="flex items-center justify-between px-3 py-2">
                <span className="flex items-center gap-2 text-gray-600">
                  <GitBranch className="w-4 h-4 text-amber-600" /> Approval gates
                </span>
                <span className="font-medium">
                  {summary.gates.length}
                  {summary.approvers.length > 0 && (
                    <span className="text-gray-400 font-normal"> · {summary.approvers.join(', ')}</span>
                  )}
                </span>
              </div>
              <div className="flex items-center justify-between px-3 py-2">
                <span className="flex items-center gap-2 text-gray-600">
                  <Wrench className="w-4 h-4 text-blue-600" /> Provision steps
                </span>
                <span className="font-medium">{summary.steps.length}</span>
              </div>
              <div className="flex items-center justify-between px-3 py-2">
                <span className="flex items-center gap-2 text-gray-600">
                  <ShieldAlert className="w-4 h-4 text-rose-500" /> Mutating actions
                </span>
                <span className={`font-medium ${summary.mutating.length ? 'text-rose-600' : ''}`}>
                  {summary.mutating.length}
                </span>
              </div>
              {summary.mutating.length > 0 && (
                <div className="px-3 py-2 text-[11px] text-gray-500">
                  Will write/change real resources:{' '}
                  <span className="font-mono text-gray-700">
                    {summary.mutating.map((s) => (s as { tool?: string }).tool).join(', ')}
                  </span>
                </div>
              )}
              {summary.externalTools.length > 0 && (
                <div className="px-3 py-2 text-[11px] text-amber-700 bg-amber-50">
                  {summary.externalTools.length} step(s) use tools exposed externally as MCP providers.
                </div>
              )}
            </div>
          ) : (
            <div className="text-xs text-gray-500 bg-gray-50 border border-gray-200 rounded-md px-3 py-2">
              This workflow has no workflow graph — publishing only changes its instructions/metadata
              that the agent reads.
            </div>
          )}

          <div className="border border-gray-200 rounded-lg divide-y divide-gray-100 text-sm">
            <div className="flex items-center justify-between px-3 py-2">
              <span className="flex items-center gap-2 text-gray-600">
                <Sparkles className="w-4 h-4 text-violet-500" /> Instructions quality
              </span>
              {instructions.status === 'loading' ? (
                <span className="text-gray-400 text-xs flex items-center gap-1">
                  <Loader2 className="w-3 h-3 animate-spin" /> scoring…
                </span>
              ) : instructions.status === 'unavailable' ? (
                <span className="text-gray-400 text-xs">not scored</span>
              ) : (
                <span
                  className={`font-medium ${
                    instructions.quality.score >= 70
                      ? 'text-green-700'
                      : instructions.quality.score >= 40
                        ? 'text-amber-600'
                        : 'text-red-600'
                  }`}
                >
                  {instructions.quality.score}/100
                </span>
              )}
            </div>
            <div className="flex items-center justify-between px-3 py-2">
              <span className="flex items-center gap-2 text-gray-600">
                <Compass className="w-4 h-4 text-sky-500" /> Routing line (goal)
              </span>
              {goalQuality === null ? (
                <span className="text-gray-400 text-xs">not scored</span>
              ) : (
                <span
                  className={`font-medium ${
                    goalQuality.score >= 70
                      ? 'text-green-700'
                      : goalQuality.score >= 40
                        ? 'text-amber-600'
                        : 'text-red-600'
                  }`}
                >
                  {goalQuality.score}/100
                </span>
              )}
            </div>
            <div className="flex items-center justify-between px-3 py-2">
              <span className="flex items-center gap-2 text-gray-600">
                <FlaskConical className="w-4 h-4 text-accent" /> Behavioral tests
              </span>
              {!tests || tests.health.total === 0 ? (
                <span className="text-amber-600 text-xs font-medium">none authored</span>
              ) : (
                <span
                  className={`font-medium ${
                    tests.health.ready ? 'text-green-700' : 'text-amber-600'
                  }`}
                >
                  {tests.health.passing}/{tests.health.total} passing
                  {tests.health.never_run > 0 && (
                    <span className="text-gray-400 font-normal">
                      {' '}
                      · {tests.health.never_run} never run
                    </span>
                  )}
                </span>
              )}
            </div>
          </div>

          {instructionsWarning && (
            <div className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-md px-3 py-2 flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
              <span>
                {instructionsWarning} They are the prompt the agent follows at runtime, so
                use “Generate instructions” or edit them on the Details tab before
                publishing.
              </span>
            </div>
          )}

          {goalQuality && goalQuality.score < 65 && (
            <div className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-md px-3 py-2 flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
              <span>
                {goalQuality.summary.collisions.length > 0 ? (
                  <>
                    This workflow's goal reads almost the same as{' '}
                    {goalQuality.summary.collisions.map((c, i) => (
                      <span key={c.key}>
                        {i > 0 && ', '}
                        <code className="bg-amber-100 px-1 rounded">{c.key}</code>
                      </span>
                    ))}
                    . The goal is the only thing the agent sees when it picks a workflow, so
                    two similar lines make it guess. Say what this one covers that the other
                    doesn't.
                  </>
                ) : (
                  <>
                    The goal is this workflow's entire line in the agent's capabilities menu
                    — it's what the agent matches a user's request against. Right now it's
                    too vague to route reliably. Edit it on the Details tab to say what the
                    user gets and when to pick this workflow.
                  </>
                )}
              </span>
            </div>
          )}

          {tests && !tests.health.ready && (
            <div
              className={`text-xs rounded-md px-3 py-2 flex items-start gap-2 ${
                tests.blocksPublish
                  ? 'text-red-700 bg-red-50 border border-red-200'
                  : 'text-amber-800 bg-amber-50 border border-amber-200'
              }`}
            >
              <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
              {tests.health.total === 0
                ? 'Nothing verifies that this workflow behaves the way you expect. Add cases in the Tests tab.'
                : `${tests.health.failing} failing and ${tests.health.never_run} never-run case(s).`}
              {tests.blocksPublish
                ? ' This environment requires passing tests, so publishing will be refused.'
                : ''}
            </div>
          )}

          {noRequestType && (
            <div className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-md px-3 py-2 flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
              No request type is set, so the durable executor won't route any requests to this graph.
              Set a request type on the Details tab if you expect it to run.
            </div>
          )}

          {validating && (
            <div className="text-xs text-gray-500 flex items-center gap-1.5">
              <Loader2 className="w-3.5 h-3.5 animate-spin" /> Validating workflow…
            </div>
          )}
          {!validating && valid && hasGraph && (
            <div className="text-xs text-green-700 flex items-center gap-1.5">
              <CheckCircle2 className="w-3.5 h-3.5" /> Workflow is valid.
            </div>
          )}
          {!validating && validationError && (
            <div className="text-xs text-red-600 bg-red-50 border border-red-200 rounded-md px-3 py-2 flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" /> {validationError}
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-gray-200">
          <Button variant="outline" onClick={onClose} disabled={publishing}>
            Cancel
          </Button>
          <Button onClick={confirm} disabled={!valid || validating || publishing}>
            {publishing ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Send className="w-4 h-4 mr-1" />}
            Publish
          </Button>
        </div>
      </div>
    </div>
  );
}

export default PublishConfirmModal;
