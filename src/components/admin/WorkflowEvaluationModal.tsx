import { useEffect, useState } from 'react';
import { AlertCircle, Loader2, RefreshCw, ShieldCheck, Sparkles, X } from 'lucide-react';
import { Button } from '../ui/button';
import { api } from '../../services/api';
import type {
  EvaluationFinding,
  EvaluationSeverity,
  WorkflowEvaluation,
  WorkflowGraphSpec,
} from '../../services/api';

interface Props {
  spec: WorkflowGraphSpec;
  onClose: () => void;
}

const RISK_TIER_STYLES: Record<string, string> = {
  low: 'text-green-700 bg-green-50 border-green-200',
  medium: 'text-amber-700 bg-amber-50 border-amber-200',
  high: 'text-orange-700 bg-orange-50 border-orange-200',
  critical: 'text-red-700 bg-red-50 border-red-200',
  unknown: 'text-gray-600 bg-gray-50 border-gray-200',
};

const QUALITY_TIER_STYLES: Record<string, string> = {
  excellent: 'text-green-700 bg-green-50 border-green-200',
  good: 'text-emerald-700 bg-emerald-50 border-emerald-200',
  fair: 'text-amber-700 bg-amber-50 border-amber-200',
  poor: 'text-red-700 bg-red-50 border-red-200',
};

const SEVERITY_STYLES: Record<EvaluationSeverity, string> = {
  critical: 'text-red-700 bg-red-50 border-red-200',
  high: 'text-orange-700 bg-orange-50 border-orange-200',
  medium: 'text-amber-700 bg-amber-50 border-amber-200',
  low: 'text-gray-600 bg-gray-50 border-gray-200',
  info: 'text-blue-700 bg-blue-50 border-blue-200',
};

function ScoreCard({
  label,
  hint,
  score,
  tier,
  styles,
  // For risk, a full bar is bad (red); for quality, a full bar is good (green).
  barClass,
}: {
  label: string;
  hint: string;
  score: number;
  tier: string;
  styles: Record<string, string>;
  barClass: string;
}) {
  return (
    <div className="border border-gray-200 rounded-lg p-3">
      <div className="flex items-center justify-between">
        <div className="text-xs font-semibold text-gray-600 uppercase tracking-wide">{label}</div>
        <span
          className={`text-[11px] font-medium border rounded px-1.5 py-0.5 capitalize ${
            styles[tier] || 'text-gray-600 bg-gray-50 border-gray-200'
          }`}
        >
          {tier}
        </span>
      </div>
      <div className="mt-1 flex items-end gap-1">
        <span className="text-2xl font-bold text-gray-900">{score}</span>
        <span className="text-xs text-gray-400 mb-1">/ 100</span>
      </div>
      <div className="mt-1.5 h-1.5 w-full bg-gray-100 rounded-full overflow-hidden">
        <div className={`h-full ${barClass}`} style={{ width: `${Math.max(0, Math.min(100, score))}%` }} />
      </div>
      <p className="text-[11px] text-gray-400 mt-1.5">{hint}</p>
    </div>
  );
}

function FindingRow({ f }: { f: EvaluationFinding }) {
  return (
    <div className="border border-gray-200 rounded-md p-2.5">
      <div className="flex items-center gap-2 flex-wrap">
        <span className={`text-[10px] font-medium border rounded px-1.5 py-0.5 capitalize ${SEVERITY_STYLES[f.severity]}`}>
          {f.severity}
        </span>
        <span className="text-[10px] text-gray-500 bg-gray-50 border border-gray-200 rounded px-1.5 py-0.5 capitalize">
          {f.category}
        </span>
        {f.stage && <code className="text-[11px] text-gray-600">{f.stage}</code>}
      </div>
      <div className="text-sm text-gray-800 mt-1.5">{f.message}</div>
      {f.fix && (
        <div className="text-[11px] text-gray-500 mt-1">
          <span className="font-medium text-gray-600">Fix:</span> {f.fix}
        </div>
      )}
    </div>
  );
}

export function WorkflowEvaluationModal({ spec, onClose }: Props) {
  const [result, setResult] = useState<WorkflowEvaluation | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setRunning(true);
    setError(null);
    try {
      setResult(await api.evaluateSpec(spec));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Evaluation failed');
      setResult(null);
    } finally {
      setRunning(false);
    }
  };

  // Evaluate on open — it's deterministic and side-effect free.
  useEffect(() => {
    run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-3xl max-h-[88vh] flex flex-col">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200">
          <div className="flex items-center gap-2">
            <ShieldCheck className="w-4 h-4 text-accent" />
            <h2 className="text-sm font-semibold">Evaluate workflow — safety &amp; completeness</h2>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={run} disabled={running}>
              {running ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5 mr-1" />}
              Re-evaluate
            </Button>
            <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        <div className="p-5 overflow-y-auto min-h-0 space-y-4">
          {error && (
            <div className="text-xs text-red-600 flex items-start gap-1">
              <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" /> {error}
            </div>
          )}

          {!result && running && (
            <div className="h-32 flex items-center justify-center text-sm text-gray-400">
              <Loader2 className="w-5 h-5 animate-spin mr-2" /> Evaluating…
            </div>
          )}

          {result && (
            <>
              {!result.valid && (
                <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-md px-3 py-2 flex items-start gap-2">
                  <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
                  <span>{result.error || 'Spec is structurally invalid.'} Fix it, then re-evaluate.</span>
                </div>
              )}

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <ScoreCard
                  label="Risk"
                  hint="Is this safe? Higher is riskier."
                  score={result.risk.score}
                  tier={result.risk.tier}
                  styles={RISK_TIER_STYLES}
                  barClass={
                    result.risk.score >= 70
                      ? 'bg-red-500'
                      : result.risk.score >= 45
                        ? 'bg-orange-500'
                        : result.risk.score >= 20
                          ? 'bg-amber-500'
                          : 'bg-green-500'
                  }
                />
                <ScoreCard
                  label="Quality"
                  hint="Is this complete? Higher is better."
                  score={result.quality.score}
                  tier={result.quality.tier}
                  styles={QUALITY_TIER_STYLES}
                  barClass={
                    result.quality.score >= 65
                      ? 'bg-green-500'
                      : result.quality.score >= 40
                        ? 'bg-amber-500'
                        : 'bg-red-500'
                  }
                />
              </div>

              {result.summary && (
                <div className="text-xs text-gray-600 bg-gray-50 border border-gray-200 rounded-md px-3 py-2">
                  {result.summary.step_count ?? 0} step{result.summary.step_count === 1 ? '' : 's'}
                  {' · '}
                  {result.summary.mutating_steps ?? 0} mutating
                  {' · '}
                  {result.summary.gate_count ?? 0} gate{result.summary.gate_count === 1 ? '' : 's'}
                  {result.summary.approval_gates && result.summary.approval_gates.length > 0 && (
                    <> {' · '}approvals: {result.summary.approval_gates.join(', ')}</>
                  )}
                  {result.summary.composes && result.summary.composes.length > 0 && (
                    <> {' · '}composes: {result.summary.composes.join(', ')}</>
                  )}
                </div>
              )}

              <div>
                <div className="text-xs font-semibold text-gray-600 uppercase tracking-wide mb-2">
                  Findings ({result.findings.length})
                </div>
                {result.findings.length === 0 ? (
                  <div className="text-sm text-green-700 bg-green-50 border border-green-200 rounded-md px-3 py-2 flex items-center gap-2">
                    <Sparkles className="w-4 h-4" /> No issues found — looks safe and complete.
                  </div>
                ) : (
                  <div className="space-y-2">
                    {result.findings.map((f, i) => (
                      <FindingRow key={i} f={f} />
                    ))}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default WorkflowEvaluationModal;
