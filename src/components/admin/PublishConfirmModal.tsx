import { useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  GitBranch,
  Loader2,
  Send,
  ShieldAlert,
  Wrench,
  X,
} from 'lucide-react';
import { Button } from '../ui/button';
import { api } from '../../services/api';
import type { Workflow, WorkflowGraphSpec, WorkflowTool } from '../../services/api';

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
