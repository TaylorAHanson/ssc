import { useMemo, useState } from 'react';
import {
  AlertCircle,
  CheckCircle2,
  GitBranch,
  Layers,
  Loader2,
  Play,
  Wrench,
  X,
} from 'lucide-react';
import { Button } from '../ui/button';
import { api } from '../../services/api';
import type { DryRunResult, WorkflowGraphSpec } from '../../services/api';
import { collectVarPaths } from '../../lib/workflowSpec';

interface Props {
  spec: WorkflowGraphSpec;
  onClose: () => void;
}

export function WorkflowTestModal({ spec, onClose }: Props) {
  const scaffold = useMemo(() => {
    const fields = collectVarPaths(spec);
    const obj: Record<string, string> = {};
    fields.forEach((f) => { obj[f] = ''; });
    return JSON.stringify(obj, null, 2);
  }, [spec]);

  const [input, setInput] = useState(scaffold);
  const [result, setResult] = useState<DryRunResult | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setRunning(true);
    setError(null);
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(input || '{}');
    } catch {
      setError('Sample input is not valid JSON.');
      setRunning(false);
      return;
    }
    try {
      setResult(await api.testSpec(spec, parsed));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Dry-run failed');
      setResult(null);
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-4xl max-h-[88vh] flex flex-col">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200">
          <div className="flex items-center gap-2">
            <Play className="w-4 h-4 text-accent" />
            <h2 className="text-sm font-semibold">Test workflow — dry run</h2>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-0 flex-1 min-h-0">
          {/* Sample input */}
          <div className="border-r border-gray-200 p-4 flex flex-col min-h-0">
            <div className="text-xs font-medium text-gray-600 mb-1">Sample request</div>
            <p className="text-[11px] text-gray-400 mb-2">
              Fields the workflow reads are pre-filled. No tools run and nothing is written.
            </p>
            <textarea
              className="flex-1 min-h-[220px] w-full border border-gray-300 rounded-md p-2 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-accent"
              value={input}
              onChange={(e) => setInput(e.target.value)}
            />
            <Button onClick={run} disabled={running} className="mt-3 self-start">
              {running ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Play className="w-4 h-4 mr-1" />}
              Run dry-run
            </Button>
            {error && (
              <div className="mt-2 text-xs text-red-600 flex items-start gap-1">
                <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" /> {error}
              </div>
            )}
          </div>

          {/* Projection */}
          <div className="p-4 overflow-y-auto min-h-0">
            {!result ? (
              <div className="h-full flex items-center justify-center text-sm text-gray-400 text-center px-6">
                Run the dry-run to see who approves and what each step receives.
              </div>
            ) : (
              <div className="space-y-3">
                {result.warnings && result.warnings.length > 0 && (
                  <div className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-md px-3 py-2">
                    <div className="font-medium mb-1">
                      {result.warnings.length} arg warning{result.warnings.length === 1 ? '' : 's'} — these args won't reach the tool:
                    </div>
                    <ul className="list-disc pl-4 space-y-0.5">
                      {result.warnings.map((w, i) => (
                        <li key={i}>{w}</li>
                      ))}
                    </ul>
                  </div>
                )}
                <div className="text-xs text-gray-600 bg-gray-50 border border-gray-200 rounded-md px-3 py-2">
                  <span className="font-medium">{result.requires_approval ? 'Requires human approval' : 'Fully auto-approved'}</span>
                  {' · '}
                  {result.mutating_steps} mutating step{result.mutating_steps === 1 ? '' : 's'}
                  {' · ends as '}
                  <code className="text-[11px]">{result.completed_status}</code>
                </div>
                {result.stages.map((s, i) => (
                  <div key={i} className="border border-gray-200 rounded-md p-2.5">
                    <div className="flex items-center gap-2">
                      {s.kind === 'gate' ? (
                        <GitBranch className="w-4 h-4 text-amber-600" />
                      ) : s.kind === 'subworkflow' ? (
                        <Layers className="w-4 h-4 text-indigo-600" />
                      ) : (
                        <Wrench className="w-4 h-4 text-blue-600" />
                      )}
                      <span className="text-sm font-medium">{s.name}</span>
                      {s.kind === 'gate' ? (
                        s.decision === 'auto_approve' ? (
                          <span className="ml-auto inline-flex items-center gap-1 text-[11px] text-green-700 bg-green-50 rounded px-1.5 py-0.5">
                            <CheckCircle2 className="w-3 h-3" /> auto-approved
                          </span>
                        ) : (
                          <span className="ml-auto text-[11px] text-amber-700 bg-amber-50 rounded px-1.5 py-0.5">
                            needs {s.type} approval
                          </span>
                        )
                      ) : s.kind === 'subworkflow' ? (
                        <span className="ml-auto inline-flex items-center gap-1.5">
                          <span className="text-[10px] text-indigo-700 bg-indigo-50 rounded px-1.5 py-0.5">
                            calls {s.ref || '—'}
                          </span>
                        </span>
                      ) : (
                        <span className="ml-auto inline-flex items-center gap-1.5">
                          <code className="text-[11px] text-gray-600">{s.tool}</code>
                          {s.is_mutating && (
                            <span className="text-[10px] bg-amber-50 text-amber-700 rounded px-1.5 py-0.5">mutating</span>
                          )}
                          {typeof s.fan_out === 'number' && s.fan_out !== 1 && (
                            <span className="text-[10px] bg-gray-100 text-gray-600 rounded px-1.5 py-0.5">× {s.fan_out}</span>
                          )}
                        </span>
                      )}
                    </div>
                    {s.error && (
                      <div className="text-[11px] text-red-600 mt-1.5 flex items-start gap-1">
                        <AlertCircle className="w-3 h-3 mt-0.5 shrink-0" /> {s.error}
                      </div>
                    )}
                    {s.kind === 'subworkflow' && s.input && Object.keys(s.input).length > 0 && !s.error && (
                      <div className="mt-2 space-y-1">
                        {Object.entries(s.input).map(([k, v]) => (
                          <div key={k} className="flex items-start gap-2 text-[11px]">
                            <span className="text-gray-500 font-mono min-w-[120px]">{k}</span>
                            <span className="text-gray-800 font-mono break-all">
                              {typeof v === 'string' ? v : JSON.stringify(v)}
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                    {s.kind === 'step' && s.calls && s.calls.length > 0 && !s.error && (
                      <div className="mt-2 space-y-1">
                        {Object.entries(s.calls[0]).map(([k, v]) => (
                          <div key={k} className="flex items-start gap-2 text-[11px]">
                            <span className="text-gray-500 font-mono min-w-[120px]">{k}</span>
                            <span className="text-gray-800 font-mono break-all">
                              {typeof v === 'string' ? v : JSON.stringify(v)}
                            </span>
                          </div>
                        ))}
                        {s.calls.length > 1 && (
                          <div className="text-[10px] text-gray-400">
                            +{s.calls.length - 1} more call{s.calls.length - 1 === 1 ? '' : 's'}
                            {s.truncated ? ' (truncated)' : ''}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export default WorkflowTestModal;
