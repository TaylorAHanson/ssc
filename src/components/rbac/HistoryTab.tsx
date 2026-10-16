import { Fragment, useCallback, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { ChevronDown, ChevronRight, RotateCcw } from 'lucide-react';
import { api } from '../../services/api';
import type { RbacChangeSetResult, RbacChangeSetSummary, RbacPermissions, RbacSheetInfo } from '../../types/rbac';
import { Button } from '../ui/button';
import { DiffView } from './DiffView';
import { Badge, EmptyState, ErrorBanner, Pager, Skeleton, Spinner, type Tone, type ToastMessage } from './ui';
import { useLoad } from './useLoad';
import { errorMessage, formatTime, loadSheets, relativeTime, sourceLabel } from './utils';
import { RevertDialog } from './RevertDialog';

const LIMIT = 50;

const SOURCE_TONES: Record<string, Tone> = {
  import: 'blue',
  request: 'indigo',
  admin_edit: 'amber',
  revert: 'gray',
};

/** Whether the viewer may edit every type a change set touched (and so revert it). */
function canRevert(cs: RbacChangeSetSummary, permissions: RbacPermissions): boolean {
  const editable = new Set(permissions.planes.filter((p) => p.can_edit).map((p) => p.plane));
  const planes = cs.summary.planes;
  return planes && planes.length ? planes.every((p) => editable.has(p)) : editable.size > 0;
}

function Counts({ cs }: { cs: RbacChangeSetSummary }) {
  const { created = 0, updated = 0, deleted = 0 } = cs.summary;
  const parts = [
    created ? <span key="c" className="text-green-700">{created} added</span> : null,
    updated ? <span key="u" className="text-amber-700">{updated} edited</span> : null,
    deleted ? <span key="d" className="text-red-700">{deleted} deleted</span> : null,
  ].filter(Boolean);
  if (parts.length === 0) return <span className="text-gray-400">No rows</span>;
  return <span className="flex flex-wrap gap-x-2">{parts}</span>;
}

export function HistoryTab({ permissions, notify }: {
  permissions: RbacPermissions;
  notify: (toast: Omit<ToastMessage, 'id'>) => void;
}) {
  const [offset, setOffset] = useState(0);
  const load = useMemo(() => () => api.getRbacHistory(offset, LIMIT), [offset]);
  const history = useLoad(load);
  const sheetsState = useLoad(loadSheets);
  const sheets = sheetsState.data ?? [];
  const [expanded, setExpanded] = useState<string | null>(null);
  const [reverting, setReverting] = useState<RbacChangeSetSummary | null>(null);

  const onReverted = (result: RbacChangeSetResult) => {
    setReverting(null);
    history.reload();
    if (result.request_id) {
      notify({
        text: 'Revert request filed. Another approver must approve it.',
        link: { to: `/requests/${result.request_id}`, label: 'View request' },
      });
    } else {
      notify({ text: `Reverted${result.change_set_id ? ` as change set ${result.change_set_id.slice(0, 8)}` : ''}.` });
    }
  };

  if (history.error) {
    return <ErrorBanner message={errorMessage(history.error, 'Could not load the history')} onRetry={history.reload} />;
  }
  if (!history.data) return <Skeleton rows={6} />;
  const { change_sets: changeSets, total } = history.data;
  if (total === 0) {
    return (
      <EmptyState title="No changes yet">
        Every import, approved request, direct edit and revert is recorded here with who made it, why, and the diff.
      </EmptyState>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-gray-500">
          Every change to the rules, newest first. Expand one to see its diff; revert records the opposite change.
        </p>
        {history.loading && <Spinner label="Refreshing…" className="text-xs" />}
      </div>
      <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
        <table className="w-full text-sm">
          <thead className="border-b bg-gray-50 text-left text-xs text-gray-500">
            <tr>
              <th scope="col" className="w-8 px-2 py-2"><span className="sr-only">Expand</span></th>
              <th scope="col" className="px-3 py-2 font-medium">When</th>
              <th scope="col" className="px-3 py-2 font-medium">Source</th>
              <th scope="col" className="px-3 py-2 font-medium">By</th>
              <th scope="col" className="px-3 py-2 font-medium">Reason</th>
              <th scope="col" className="px-3 py-2 font-medium">Rows</th>
              <th scope="col" className="px-3 py-2 font-medium">Sheets</th>
              <th scope="col" className="px-3 py-2 text-right font-medium"><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            {changeSets.map((cs) => {
              const open = expanded === cs.id;
              const sheetNames = cs.summary.sheets ?? [];
              const allowed = canRevert(cs, permissions);
              return (
                <Fragment key={cs.id}>
                  <tr className="border-b align-top hover:bg-gray-50/50">
                    <td className="px-2 py-2">
                      <button
                        type="button"
                        onClick={() => setExpanded(open ? null : cs.id)}
                        aria-expanded={open}
                        aria-label={open ? 'Hide the diff' : 'Show the diff'}
                        className="text-gray-400 hover:text-gray-700"
                      >
                        {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                      </button>
                    </td>
                    <td className="whitespace-nowrap px-3 py-2" title={relativeTime(cs.created_at)}>{formatTime(cs.created_at)}</td>
                    <td className="px-3 py-2">
                      <div className="flex flex-col items-start gap-1">
                        <Badge tone={SOURCE_TONES[cs.source] ?? 'gray'}>{sourceLabel(cs.source)}</Badge>
                        {cs.source === 'request' && cs.source_ref && (
                          <Link to={`/requests/${cs.source_ref}`} className="text-xs text-primary hover:underline">View request</Link>
                        )}
                        {cs.reverted_by && (
                          <Badge tone="red" title={`Reverted by change set ${cs.reverted_by}`}>Reverted</Badge>
                        )}
                      </div>
                    </td>
                    <td className="px-3 py-2 text-gray-700">{cs.actor || '—'}</td>
                    <td className="max-w-xs px-3 py-2 text-gray-700">
                      <span className="line-clamp-2" title={cs.reason || undefined}>{cs.reason || '—'}</span>
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 text-xs"><Counts cs={cs} /></td>
                    <td className="max-w-[16rem] px-3 py-2 text-xs text-gray-600">
                      <span className="line-clamp-2 font-mono" title={sheetNames.join(', ')}>{sheetNames.join(', ') || '—'}</span>
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 text-right">
                      {!cs.reverted_by && (
                        <Button
                          size="sm"
                          variant="outline"
                          className="gap-1.5"
                          disabled={!allowed}
                          title={allowed ? undefined : 'You can only revert changes to types you can edit'}
                          onClick={() => setReverting(cs)}
                        >
                          <RotateCcw className="h-3.5 w-3.5" /> Revert
                        </Button>
                      )}
                    </td>
                  </tr>
                  {open && (
                    <tr className="border-b bg-gray-50/60">
                      <td colSpan={8} className="px-4 py-3">
                        <ChangeSetDiffs id={cs.id} sheets={sheets} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      <Pager offset={offset} limit={LIMIT} total={total} onChange={(next) => { setExpanded(null); setOffset(next); }} />
      {reverting && (
        <RevertDialog
          changeSet={reverting}
          sheets={sheets}
          minReasonLength={permissions.min_reason_length}
          onClose={() => setReverting(null)}
          onDone={onReverted}
        />
      )}
    </div>
  );
}

function ChangeSetDiffs({ id, sheets }: { id: string; sheets: RbacSheetInfo[] }) {
  const load = useCallback(() => api.getRbacChangeSet(id), [id]);
  const detail = useLoad(load);
  if (detail.error) return <ErrorBanner message={errorMessage(detail.error, 'Could not load the change set')} onRetry={detail.reload} />;
  if (!detail.data) return <Spinner label="Loading the diff…" />;
  return (
    <div className="space-y-2">
      {detail.data.reason && (
        <p className="text-sm text-gray-700"><span className="font-medium">Reason:</span> {detail.data.reason}</p>
      )}
      <p className="text-xs text-gray-500">Change set <code>{detail.data.id}</code></p>
      <DiffView diffs={detail.data.diffs} sheets={sheets} />
    </div>
  );
}
