import { useEffect, useMemo, useRef, useState } from 'react';
import { Play, ShieldAlert } from 'lucide-react';
import { clearPausedRun, listRuns, resumePausedRun, startRun } from '../../../services/metadataApi';
import type { MetadataConfig, ReconcileResult, RunRequest } from '../../../types/metadata';
import { Button } from '../../ui/button';
import { EmptyState, ErrorBanner, Modal, Notice, Pager, Skeleton } from '../../rbac/ui';
import { RunStatusBadge } from './badges';
import { useMetadata } from './context';
import { errorMessage, formatTime, limitedSchemas, plural, relativeTime } from './format';
import { CircuitBreakerBanner } from './ModeBanner';
import { useAsync } from './useAsync';

const PAGE_SIZE = 20;

const TRIGGER_LABELS: Record<string, string> = {
  queue: 'After an edit here',
  sweep: 'Scheduled check',
  manual: 'Started by an admin',
  new_object: 'Changed in Unity Catalog',
  notify: 'Asked to check',
};

/** Sync history: what each check read, planned and wrote; admins can start or resume one. */
export function RunsTab() {
  const { config, isAdmin, refreshToken, bumpRefresh, notify, openRun } = useMetadata();
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState<RunRequest['kind'] | 'resume' | 'clear' | null>(null);
  const [confirmResume, setConfirmResume] = useState(false);
  const load = useMemo(() => () => listRuns({ page, page_size: PAGE_SIZE }), [page]);
  const { data, error, loading, reload } = useAsync(load, { keepPrevious: true, token: refreshToken });
  const pausedId = config.circuit_breaker_run_id ?? null;
  const paused = pausedId ? data?.items.find((r) => r.run_id === pausedId) : undefined;
  const running = data?.items.find((r) => r.status === 'running');
  const runningId = running?.run_id;
  const inFlight = !!busy || !!running;
  const watching = useRef<string | null>(null);

  useEffect(() => {
    if (!runningId) return;
    const id = window.setInterval(() => {
      reload();
    }, 2500);
    return () => window.clearInterval(id);
  }, [runningId, reload]);

  const run = async (request: RunRequest | 'resume', label: typeof busy) => {
    setBusy(label);
    try {
      const result = request === 'resume' ? await resumePausedRun(pausedId ?? '') : await startRun(request);
      bumpRefresh();
      if (result.status === 'running') {
        watching.current = result.run_id;
        notify('Checking Unity Catalog. A large catalog takes a few minutes — this list updates when it finishes.');
      } else {
        notify(runMessage(result));
        openRun(result.run_id);
      }
    } catch (e) {
      notify(errorMessage(e, 'Couldn’t start the sync'), { kind: 'error' });
    } finally {
      setBusy(null);
      setConfirmResume(false);
    }
  };

  const clearPause = async () => {
    setBusy('clear');
    try {
      await clearPausedRun(pausedId ?? '');
      bumpRefresh();
      notify('Syncing resumed without applying the paused removals. If what caused them is still saved here, the next check pauses again.');
    } catch (e) {
      notify(errorMessage(e, 'Couldn’t clear the pause'), { kind: 'error' });
    } finally {
      setBusy(null);
      setConfirmResume(false);
    }
  };

  useEffect(() => {
    const id = watching.current;
    if (!id) return;
    const row = data?.items.find((r) => r.run_id === id);
    if (!row || row.status === 'running') return;
    watching.current = null;
    notify(runMessage(row));
    openRun(row.run_id);
  }, [data, notify, openRun]);

  return (
    <div className="space-y-3">
      <CircuitBreakerBanner
        action={
          isAdmin &&
          pausedId && (
            <Button size="sm" variant="outline" className="gap-1.5 bg-white" onClick={() => setConfirmResume(true)}>
              <ShieldAlert className="h-3.5 w-3.5" /> {config.can_resume_sync ? 'Review and resume' : 'Review the paused sync'}
            </Button>
          )
        }
      />
      {isAdmin && (
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            className="gap-1.5"
            disabled={inFlight || config.reconcile_mode === 'off'}
            onClick={() => run({ kind: 'sweep' }, 'sweep')}
          >
            <Play className="h-3.5 w-3.5" /> {busy === 'sweep' || running ? 'Checking…' : 'Check now'}
          </Button>
          <span className="text-xs text-gray-500">
            {running ? 'A check is running. Large catalogs take a few minutes; you can leave this page.' : checkNowNote(config)}
          </span>
        </div>
      )}

      {error ? <ErrorBanner message={errorMessage(error, 'Couldn’t load the sync history')} onRetry={reload} /> : null}
      {!data && loading ? (
        <Skeleton rows={6} />
      ) : data && data.items.length === 0 ? (
        <EmptyState title="No syncs yet">They appear here once Unity Catalog has been checked.</EmptyState>
      ) : data ? (
        <div className={loading ? 'overflow-x-auto opacity-60' : 'overflow-x-auto'}>
          <p className="mb-2 text-xs text-gray-500">
            Each row counts only what that check looked at. A full check reads whole catalogs; the others re-check just the objects that
            were edited here or changed in Unity Catalog. What’s out of sync right now is listed under Out of sync with Unity Catalog.
          </p>
          <table className="w-full rounded-lg border border-gray-200 bg-white text-left text-sm">
            <thead className="bg-gray-50 text-xs text-gray-600">
              <tr>
                <th scope="col" className="px-3 py-2 font-medium">Started</th>
                <th scope="col" className="px-3 py-2 font-medium">Why</th>
                <th scope="col" className="px-3 py-2 font-medium">Status</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Checked</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Out of sync</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Written</th>
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
                    {r.mode === 'plan' && <span className="ml-1 text-gray-400">(preview)</span>}
                    {r.actor && <span className="block text-gray-400">{r.actor}</span>}
                  </td>
                  <td className="px-3 py-2">
                    <RunStatusBadge status={r.status} />
                    {!!r.unread_catalogs?.length && (
                      <span className="mt-0.5 block text-xs text-amber-800">Couldn’t read {r.unread_catalogs.join(', ')}</span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {r.objects_checked.toLocaleString()}
                    {r.status !== 'skipped' && r.status !== 'paused' && (
                      <span className="block text-xs text-gray-400">{r.full_check ? 'full check' : 'only these objects'}</span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">{r.objects_drifted.toLocaleString()}</td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {r.statements_applied}/{r.statements_planned}
                    {r.statements_failed > 0 && <span className="ml-1 text-red-700">({r.statements_failed} failed)</span>}
                    {!!r.statements_reported && <span className="block text-xs text-gray-400">+{r.statements_reported} report only</span>}
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

      {confirmResume && pausedId && (
        <Modal
          title="Syncing is paused"
          onClose={() => setConfirmResume(false)}
          footer={
            <>
              <Button variant="outline" onClick={() => setConfirmResume(false)}>
                Cancel
              </Button>
              {config.can_resume_sync && (
                <>
                  <Button variant="outline" onClick={clearPause} disabled={!!busy}>
                    {busy === 'clear' ? 'Clearing…' : 'Clear without applying'}
                  </Button>
                  <Button onClick={() => run('resume', 'resume')} disabled={!!busy || config.reconcile_mode !== 'enforce'}>
                    {busy === 'resume' ? 'Resuming…' : 'Apply these removals'}
                  </Button>
                </>
              )}
            </>
          }
        >
          <Notice kind="warn">
            {paused ? (
              <>
                The paused sync planned {plural(paused.removals_planned, 'removal')}.
                {paused.paused_reason && <span className="mt-1 block">{paused.paused_reason}</span>}
              </>
            ) : (
              'A sync planned more removals than the safety limit allows.'
            )}
          </Notice>
          <p className="text-sm text-gray-700">
            <strong>Apply these removals</strong> re-checks only the objects in the paused sync and writes their changes, as long as that removes no more
            than the sync planned. If it would remove more, nothing is written and the new sync pauses for review instead.
          </p>
          <p className="text-sm text-gray-700">
            <strong>Clear without applying</strong> lifts the pause and writes nothing. Fix what caused the removals first (for example, undo the change
            set, or use “Keep Unity Catalog’s values” under Out of sync), or the next check pauses again.
          </p>
          {!config.can_resume_sync && <p className="text-sm text-gray-700">Only a Platform Admin can apply or clear a paused sync.</p>}
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              setConfirmResume(false);
              openRun(pausedId);
            }}
          >
            Open the paused sync
          </Button>
        </Modal>
      )}
    </div>
  );
}

function runMessage(r: ReconcileResult): string {
  if (r.status === 'paused') return `The sync paused: it would remove ${plural(r.removals_planned, 'tag')}, which is over the safety limit.`;
  if (r.unread_catalogs?.length && r.objects_checked === 0) {
    return `Couldn’t read ${r.unread_catalogs.join(', ')} from Unity Catalog, so nothing was checked. The sync’s details say why.`;
  }
  if (r.mode === 'plan') return `Check finished: ${plural(r.statements_planned, 'statement')} would run once syncing is on.`;
  return `Sync finished: ${r.statements_applied} of ${plural(r.statements_planned, 'statement')} written.`;
}

/** What Check now does in the current mode. */
function checkNowNote(config: MetadataConfig): string {
  if (config.reconcile_mode === 'off') return 'Syncing is off, so Unity Catalog isn’t checked. Turn it on under Admin → Settings (Metadata Manager).';
  if (config.reconcile_mode === 'enforce') {
    const schemas = limitedSchemas(config);
    return `Checks Unity Catalog and writes the differences${schemas.length ? ` in ${schemas.join(', ')}` : ''}.`;
  }
  return 'Checks Unity Catalog and lists what syncing would write. Nothing is written while syncing is report only.';
}
