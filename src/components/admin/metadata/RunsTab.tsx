import { useMemo, useState } from 'react';
import { Play, ScanSearch, ShieldAlert } from 'lucide-react';
import { listRuns, startRun } from '../../../services/metadataApi';
import type { ReconcileResult, RunRequest } from '../../../types/metadata';
import { Button } from '../../ui/button';
import { EmptyState, ErrorBanner, Modal, Notice, Pager, Skeleton } from '../../rbac/ui';
import { RunStatusBadge } from './badges';
import { useMetadata } from './context';
import { errorMessage, formatTime, plural, relativeTime } from './format';
import { CircuitBreakerBanner, ModeBanner } from './ModeBanner';
import { useAsync } from './useAsync';

const PAGE_SIZE = 20;

const TRIGGER_LABELS: Record<string, string> = {
  queue: 'After a change',
  sweep: 'Scheduled check',
  manual: 'Started by an admin',
  new_object: 'New object found',
  notify: 'Asked to check',
};

/** Reconciler runs: what each checked, planned and applied; admins can start or resume runs. */
export function RunsTab() {
  const { isAdmin, refreshToken, bumpRefresh, notify, openRun } = useMetadata();
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState<RunRequest['kind'] | 'resume' | null>(null);
  const [confirmResume, setConfirmResume] = useState(false);
  const load = useMemo(() => () => listRuns({ page, page_size: PAGE_SIZE }), [page]);
  const { data, error, loading, reload } = useAsync(load, { keepPrevious: true, token: refreshToken });
  const paused = data?.items.find((r) => r.status === 'paused');

  const run = async (request: RunRequest, label: typeof busy) => {
    setBusy(label);
    try {
      const result = await startRun(request);
      notify(runMessage(result));
      bumpRefresh();
      openRun(result.run_id);
    } catch (e) {
      notify(errorMessage(e, 'Couldn’t start the run'), { kind: 'error' });
    } finally {
      setBusy(null);
      setConfirmResume(false);
    }
  };

  return (
    <div className="space-y-3">
      <ModeBanner />
      <CircuitBreakerBanner
        action={
          isAdmin && (
            <Button size="sm" variant="outline" className="gap-1.5 bg-white" onClick={() => setConfirmResume(true)}>
              <ShieldAlert className="h-3.5 w-3.5" /> Review and resume
            </Button>
          )
        }
      />
      {isAdmin && (
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="outline" className="gap-1.5" disabled={!!busy} onClick={() => run({ kind: 'plan' }, 'plan')}>
            <ScanSearch className="h-3.5 w-3.5" /> {busy === 'plan' ? 'Planning…' : 'Run a plan'}
          </Button>
          <Button size="sm" className="gap-1.5" disabled={!!busy} onClick={() => run({ kind: 'sweep' }, 'sweep')}>
            <Play className="h-3.5 w-3.5" /> {busy === 'sweep' ? 'Running…' : 'Run a full check now'}
          </Button>
          <span className="text-xs text-gray-500">A plan only reports what would change. A full check applies it (in enforce mode).</span>
        </div>
      )}

      {error ? <ErrorBanner message={errorMessage(error, 'Couldn’t load runs')} onRetry={reload} /> : null}
      {!data && loading ? (
        <Skeleton rows={6} />
      ) : data && data.items.length === 0 ? (
        <EmptyState title="No runs yet">Runs appear here once the reconciler checks Unity Catalog.</EmptyState>
      ) : data ? (
        <div className={loading ? 'overflow-x-auto opacity-60' : 'overflow-x-auto'}>
          <table className="w-full rounded-lg border border-gray-200 bg-white text-left text-sm">
            <thead className="bg-gray-50 text-xs text-gray-600">
              <tr>
                <th scope="col" className="px-3 py-2 font-medium">Started</th>
                <th scope="col" className="px-3 py-2 font-medium">Why</th>
                <th scope="col" className="px-3 py-2 font-medium">Status</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Checked</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Drifted</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Statements</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Removals</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((r) => (
                <tr key={r.run_id} className="border-t border-gray-100">
                  <td className="px-3 py-2">
                    <button type="button" onClick={() => openRun(r.run_id)} className="text-left text-primary hover:underline" title={formatTime(r.started_at)}>
                      {relativeTime(r.started_at) || r.run_id}
                    </button>
                  </td>
                  <td className="px-3 py-2 text-xs text-gray-600">
                    {TRIGGER_LABELS[r.trigger] ?? r.trigger}
                    {r.mode === 'plan' && <span className="ml-1 text-gray-400">(plan only)</span>}
                    {r.actor && <span className="block text-gray-400">{r.actor}</span>}
                  </td>
                  <td className="px-3 py-2">
                    <RunStatusBadge status={r.status} />
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">{r.objects_checked.toLocaleString()}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{r.objects_drifted.toLocaleString()}</td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {r.statements_applied}/{r.statements_planned}
                    {r.statements_failed > 0 && <span className="ml-1 text-red-700">({r.statements_failed} failed)</span>}
                  </td>
                  <td className={r.removals_planned > 0 ? 'px-3 py-2 text-right tabular-nums text-amber-800' : 'px-3 py-2 text-right tabular-nums'}>
                    {r.removals_planned}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {data && <Pager offset={(page - 1) * PAGE_SIZE} limit={PAGE_SIZE} total={data.total} onChange={(o) => setPage(Math.floor(o / PAGE_SIZE) + 1)} />}

      {confirmResume && (
        <Modal
          title="Resume the reconciler?"
          onClose={() => setConfirmResume(false)}
          footer={
            <>
              <Button variant="outline" onClick={() => setConfirmResume(false)}>
                Cancel
              </Button>
              <Button onClick={() => run({ kind: 'sweep', override_circuit_breaker: true }, 'resume')} disabled={busy === 'resume'}>
                {busy === 'resume' ? 'Resuming…' : 'Apply anyway and resume'}
              </Button>
            </>
          }
        >
          <Notice kind="warn">
            {paused ? (
              <>
                The paused run planned {plural(paused.removals_planned, 'removal')}.
                {paused.paused_reason && <span className="mt-1 block">{paused.paused_reason}</span>}
              </>
            ) : (
              'The last run planned more removals than the safety limit allows.'
            )}
          </Notice>
          <p className="text-sm text-gray-700">
            Resuming runs a full check that ignores the limit once, so every planned removal is written to Unity Catalog. Check the paused run’s statements
            first; if the removals are wrong, fix the declared tags instead (for example by keeping Unity Catalog’s values on the Drift tab).
          </p>
          {paused && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setConfirmResume(false);
                openRun(paused.run_id);
              }}
            >
              Open the paused run
            </Button>
          )}
        </Modal>
      )}
    </div>
  );
}

function runMessage(r: ReconcileResult): string {
  if (r.status === 'paused') return `The run paused: it would remove ${plural(r.removals_planned, 'tag')}, which is over the safety limit.`;
  if (r.mode === 'plan') return `Plan finished: ${plural(r.statements_planned, 'statement')} would run.`;
  return `Run finished: ${r.statements_applied} of ${plural(r.statements_planned, 'statement')} applied.`;
}
