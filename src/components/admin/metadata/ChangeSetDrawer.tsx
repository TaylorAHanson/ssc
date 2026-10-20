import { useCallback } from 'react';
import { Link } from 'react-router-dom';
import { Undo2 } from 'lucide-react';
import { getChangeSet } from '../../../services/metadataApi';
import type { ChangeSet } from '../../../types/metadata';
import { Button } from '../../ui/button';
import { Drawer, ErrorBanner, Notice, SectionTitle, Skeleton } from '../../rbac/ui';
import { ActorTypeBadge, SourceBadge } from './badges';
import { useMetadata } from './context';
import { EventList } from './EventList';
import { changeSetCounts, errorMessage, eventsByObject, formatTime } from './format';
import { useAsync } from './useAsync';

/** One saved change: every before → after it made, and a way to undo it. */
export function ChangeSetDrawer({ id, onClose, onRevert }: { id: string; onClose: () => void; onRevert: (cs: ChangeSet) => void }) {
  const { refreshToken, openChangeSet } = useMetadata();
  const load = useCallback(() => getChangeSet(id), [id]);
  const { data: cs, error, loading, reload } = useAsync(load, { token: refreshToken });

  return (
    <Drawer
      title="Change details"
      subtitle={cs && <span title={formatTime(cs.created_at)}>{formatTime(cs.created_at)}</span>}
      onClose={onClose}
      footer={
        cs && (
          <div className="flex items-center justify-between gap-3">
            <span className="text-xs text-gray-500">
              {cs.reverted_by ? 'This change has already been undone.' : 'Undoing creates a new change that puts the earlier values back.'}
            </span>
            <Button size="sm" variant="outline" className="gap-1.5" disabled={!!cs.reverted_by || !cs.events.length} onClick={() => onRevert(cs)}>
              <Undo2 className="h-3.5 w-3.5" /> Undo this change
            </Button>
          </div>
        )
      }
    >
      {error ? <ErrorBanner message={errorMessage(error, 'Couldn’t load this change')} onRetry={reload} /> : null}
      {!cs && loading && <Skeleton rows={5} />}
      {cs && (
        <>
          <dl className="grid grid-cols-[auto,1fr] gap-x-4 gap-y-1.5 text-sm">
            <dt className="text-gray-500">Who</dt>
            <dd className="flex flex-wrap items-center gap-1.5">
              <span className="font-medium text-gray-900">{cs.actor}</span>
              <ActorTypeBadge type={cs.actor_type} />
            </dd>
            <dt className="text-gray-500">Made with</dt>
            <dd>
              <SourceBadge source={cs.source} />
            </dd>
            <dt className="text-gray-500">Reason</dt>
            <dd className="whitespace-pre-wrap text-gray-800">{cs.reason || <span className="text-gray-400">None given</span>}</dd>
            <dt className="text-gray-500">Changed</dt>
            <dd className="text-gray-800">{changeSetCounts(cs).join(' · ') || 'Nothing'}</dd>
            {cs.request_id && (
              <>
                <dt className="text-gray-500">Approval</dt>
                <dd>
                  <Link to={`/requests/${cs.request_id}`} className="text-primary hover:underline">
                    Open the approval request
                  </Link>
                </dd>
              </>
            )}
          </dl>
          {cs.reverted_by && (
            <Notice kind="warn">
              This change was undone later.{' '}
              <button type="button" onClick={() => openChangeSet(cs.reverted_by as string)} className="font-medium underline">
                See the undo
              </button>
            </Notice>
          )}
          {cs.reverts_change_set_id && (
            <Notice kind="info">
              This change undid an earlier one.{' '}
              <button type="button" onClick={() => openChangeSet(cs.reverts_change_set_id as string)} className="font-medium underline">
                See the original
              </button>
            </Notice>
          )}
          {eventsByObject(cs.events).map(([fqn, events]) => (
            <section key={fqn}>
              <SectionTitle>{events[0].name ?? fqn.replace('::', '.')}</SectionTitle>
              <EventList events={events} showActor={false} showChangeSet={false} />
            </section>
          ))}
          {!cs.events.length && <p className="text-sm text-gray-500">This change didn’t record any edits.</p>}
        </>
      )}
    </Drawer>
  );
}
