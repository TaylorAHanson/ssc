import { useCallback, useState } from 'react';
import { getRun } from '../../../services/metadataApi';
import type { PlanStatus } from '../../../types/metadata';
import { Drawer, ErrorBanner, Notice, SectionTitle, Skeleton } from '../../rbac/ui';
import { RunStatusBadge } from './badges';
import { useMetadata } from './context';
import { errorMessage, formatTime } from './format';
import { PlanList } from './ObjectSections';
import { useAsync } from './useAsync';

const MAX_ITEMS = 200;

/** One reconciler run: totals, why it stopped, and every planned statement with its SQL. */
export function RunDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const { refreshToken } = useMetadata();
  const load = useCallback(() => getRun(id), [id]);
  const { data: run, error, loading, reload } = useAsync(load, { token: refreshToken });
  const [status, setStatus] = useState<PlanStatus | ''>('');
  const items = run ? run.items.filter((i) => !status || i.status === status) : [];

  return (
    <Drawer title="Reconciler run" subtitle={run && <span className="font-mono text-xs">{run.run_id}</span>} onClose={onClose}>
      {error ? <ErrorBanner message={errorMessage(error, 'Couldn’t load this run')} onRetry={reload} /> : null}
      {!run && loading && <Skeleton rows={5} />}
      {run && (
        <>
          <dl className="grid grid-cols-[auto,1fr] gap-x-4 gap-y-1 text-sm">
            <dt className="text-gray-500">Status</dt>
            <dd>
              <RunStatusBadge status={run.status} /> <span className="text-xs text-gray-500">({run.mode} mode)</span>
            </dd>
            <dt className="text-gray-500">Started</dt>
            <dd>{formatTime(run.started_at)}</dd>
            <dt className="text-gray-500">Finished</dt>
            <dd>{formatTime(run.finished_at)}</dd>
            {run.actor && (
              <>
                <dt className="text-gray-500">Started by</dt>
                <dd>{run.actor}</dd>
              </>
            )}
            <dt className="text-gray-500">Objects</dt>
            <dd>
              {run.objects_checked.toLocaleString()} checked, {run.objects_drifted.toLocaleString()} drifted
            </dd>
            <dt className="text-gray-500">Statements</dt>
            <dd>
              {run.statements_planned} planned, {run.statements_applied} applied, {run.statements_failed} failed; {run.removals_planned} tag removals
            </dd>
          </dl>
          {run.paused_reason && <Notice kind="error" title="Paused by the safety limit">{run.paused_reason}</Notice>}
          {run.error && <Notice kind="error" title="The run failed">{run.error}</Notice>}
          {run.skipped_catalogs.length > 0 && (
            <Notice kind="warn" title="Some catalogs were skipped">
              {run.skipped_catalogs.join(', ')}. Usually the service principal can’t read or tag them.
            </Notice>
          )}
          <section>
            <SectionTitle
              actions={
                run.items.length > 0 && (
                  <select
                    value={status}
                    onChange={(e) => setStatus(e.target.value as PlanStatus | '')}
                    aria-label="Show statements"
                    className="h-8 rounded-md border border-gray-300 bg-white px-2 text-xs"
                  >
                    <option value="">All statements ({run.items.length})</option>
                    {(['planned', 'applied', 'failed', 'skipped', 'noop'] as PlanStatus[])
                      .filter((s) => run.items.some((i) => i.status === s))
                      .map((s) => (
                        <option key={s} value={s}>
                          {s} ({run.items.filter((i) => i.status === s).length})
                        </option>
                      ))}
                  </select>
                )
              }
            >
              Statements
            </SectionTitle>
            {items.length ? <PlanList items={items.slice(0, MAX_ITEMS)} showObject /> : <p className="text-sm text-gray-500">Nothing needed changing.</p>}
            {items.length > MAX_ITEMS && <p className="mt-2 text-xs text-gray-500">Showing the first {MAX_ITEMS} of {items.length.toLocaleString()}.</p>}
          </section>
        </>
      )}
    </Drawer>
  );
}
