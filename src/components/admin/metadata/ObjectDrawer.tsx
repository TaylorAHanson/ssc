import { useCallback, useState } from 'react';
import { ArrowUpRight, PencilLine, RefreshCw } from 'lucide-react';
import { getObject, notifyReconcile } from '../../../services/metadataApi';
import type { ObjectDetail } from '../../../types/metadata';
import { Button } from '../../ui/button';
import { Badge, Drawer, ErrorBanner, Notice, SectionTitle, Skeleton } from '../../rbac/ui';
import { ApplyBadge, DriftBadge, EnvBadge, StateBadge } from './badges';
import { useMetadata } from './context';
import { EventList } from './EventList';
import { TYPE_LABELS, errorMessage, objectLabel } from './format';
import { ApplySection, ColumnsSection, CounterpartsSection, DescriptionSection, DesiredVsActual } from './ObjectSections';
import { seedFromDetail, seedFromSummary, useStagingStore } from './staging';
import { TargetEditor } from './TagEditor';
import { useAsync } from './useAsync';

/** Everything about one object: declared vs actual, authorship, columns, history and apply status. */
export function ObjectDrawer({ name, column, onClose, onPromote }: {
  name: string;
  column: string | null;
  onClose: () => void;
  onPromote: (detail: ObjectDetail) => void;
}) {
  const { refreshToken, notify, config } = useMetadata();
  const load = useCallback(() => getObject(name, column), [name, column]);
  const { data, error, loading, reload } = useAsync(load, { token: refreshToken });
  const stage = useStagingStore((s) => s.stage);
  const staged = useStagingStore((s) => (data ? s.targets[data.fqn_key] : undefined));
  const [checking, setChecking] = useState(false);

  const desired = data?.desired?.status === 'active' ? data.desired : null;
  const title = objectLabel(name, column);
  const type = desired?.securable_type ?? data?.observed?.securable_type;
  const driftCount = data?.drift.filter((d) => !d.exempt).length ?? 0;

  const checkNow = async () => {
    if (!data) return;
    setChecking(true);
    try {
      const res = await notifyReconcile({ names: [data.fqn_key] });
      notify(res.enqueued.length ? 'Asked the reconciler to check this object now.' : 'The reconciler didn’t accept this object.', {
        kind: res.enqueued.length ? 'success' : 'error',
      });
      window.setTimeout(reload, 3000);
    } catch (e) {
      notify(errorMessage(e), { kind: 'error' });
    } finally {
      setChecking(false);
    }
  };

  return (
    <Drawer
      title={<span className="break-all font-mono text-base">{title}</span>}
      subtitle={
        data && (
          <span className="flex flex-wrap items-center gap-1.5">
            {type && <span>{TYPE_LABELS[type]}</span>}
            <EnvBadge env={desired?.environment} />
            <StateBadge state={desired?.object_state} managed={!!desired} />
            {driftCount > 0 && <DriftBadge />}
            {desired?.last_apply_status && desired.last_apply_status !== 'applied' && <ApplyBadge status={desired.last_apply_status} />}
          </span>
        )
      }
      onClose={onClose}
    >
      {error ? <ErrorBanner message={errorMessage(error, 'Couldn’t load this object')} onRetry={reload} /> : null}
      {!data && loading && <Skeleton rows={6} />}
      {data && (
        <>
          <div className="flex flex-wrap gap-2">
            {!staged && (
              <Button size="sm" className="gap-1.5" onClick={() => stage([seedFromDetail(data)])} data-autofocus>
                <PencilLine className="h-3.5 w-3.5" /> Edit tags and description
              </Button>
            )}
            {Object.keys(data.counterparts).length > 0 && desired && (
              <Button size="sm" variant="outline" className="gap-1.5" onClick={() => onPromote(data)}>
                <ArrowUpRight className="h-3.5 w-3.5" /> Copy to another environment
              </Button>
            )}
            {desired && config.reconcile_mode !== 'off' && (
              <Button size="sm" variant="outline" className="gap-1.5" onClick={checkNow} disabled={checking}>
                <RefreshCw className="h-3.5 w-3.5" /> {checking ? 'Asking…' : 'Check Unity Catalog now'}
              </Button>
            )}
          </div>

          <Notice kind={data.editable ? 'success' : 'info'} title={data.editable ? 'You can edit this object' : 'Your edits here need approval'}>
            {data.edit_reasons.length > 0 && (
              <ul className="list-disc pl-5">
                {data.edit_reasons.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            )}
          </Notice>

          {staged && (
            <section className="rounded-md border border-indigo-200 bg-indigo-50/40 p-3">
              <SectionTitle>Your staged edits</SectionTitle>
              <TargetEditor target={staged} />
              <p className="mt-2 text-xs text-gray-500">Edits stay staged until you review and submit them from the tray at the bottom of the Objects tab.</p>
            </section>
          )}

          <section>
            <SectionTitle>Owners</SectionTitle>
            {data.owner_groups.length ? (
              <p className="flex flex-wrap items-center gap-1.5 text-sm">
                {data.owner_groups.map((g) => (
                  <Badge key={g} tone="indigo">
                    {g}
                  </Badge>
                ))}
                {data.owner_source && <span className="text-xs text-gray-500">from the {config.owner_tag_key} tag on {data.owner_source}</span>}
              </p>
            ) : (
              <p className="text-sm text-gray-500">
                No owner group. Set a <code>{config.owner_tag_key}</code> tag here or on a parent so owners can edit without an admin.
              </p>
            )}
          </section>

          <DesiredVsActual detail={data} />
          <DescriptionSection detail={data} />
          <ColumnsSection columns={data.columns} onStage={(c) => stage([seedFromSummary(c)])} />
          <CounterpartsSection counterparts={data.counterparts} />
          <ApplySection detail={data} />

          <section>
            <SectionTitle>History</SectionTitle>
            <EventList events={data.history} empty="No changes have been recorded for this object." />
          </section>
        </>
      )}
    </Drawer>
  );
}
