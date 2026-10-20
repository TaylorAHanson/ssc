import { Lock, PencilLine } from 'lucide-react';
import type { DriftEntry, ObjectDetail, ObjectSummary, PlanOutcome } from '../../../types/metadata';
import { Badge, CopyButton, Notice, SectionTitle } from '../../rbac/ui';
import { ApplyBadge, DriftBadge, EnvBadge, PlanStatusBadge, StateBadge } from './badges';
import { useMetadata } from './context';
import { AuthorLine } from './EventList';
import { DRIFT_KIND_LABELS, displayTags, formatTime, relativeTime, summaryDrift } from './format';
import { TagChip, TagList } from './TagChips';

function driftFor(drift: DriftEntry[], field: 'tag' | 'comment', key?: string): DriftEntry | undefined {
  return drift.find((d) => d.field === field && (field === 'comment' || d.key === key));
}

function SyncCell({ entry, pending }: { entry: DriftEntry | undefined; pending: boolean }) {
  if (pending) return <span className="text-xs text-gray-500">Applied when created</span>;
  if (!entry) return <span className="text-xs text-green-700">In sync</span>;
  if (entry.exempt) {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-gray-500" title="This key is managed outside this tool, so differences are expected.">
        <Lock className="h-3 w-3" aria-hidden="true" /> Managed elsewhere
      </span>
    );
  }
  return <span className="text-xs font-medium text-amber-800">{DRIFT_KIND_LABELS[entry.kind]}</span>;
}

/** Declared tags next to what Unity Catalog reports, with who set each declared value. */
export function DesiredVsActual({ detail }: { detail: ObjectDetail }) {
  const { config } = useMetadata();
  const desired = detail.desired?.status === 'active' ? detail.desired : null;
  const observed = detail.observed?.present ? detail.observed : null;
  const pending = desired?.object_state === 'pending';
  const keys = [...new Set([...Object.keys(desired?.tags ?? {}), ...Object.keys(observed?.tags ?? {}), ...detail.drift.filter((d) => d.key).map((d) => d.key as string)])].sort(
    (a, b) => Number(config.exempt_keys.includes(a)) - Number(config.exempt_keys.includes(b)) || a.localeCompare(b),
  );

  return (
    <section>
      <SectionTitle>Tags: declared here vs in Unity Catalog</SectionTitle>
      {!desired && (
        <Notice kind="info" className="mb-2">
          Nobody manages this object’s metadata here yet; these are the tags Unity Catalog reports. Editing it takes it under management with its current tags.
        </Notice>
      )}
      {keys.length === 0 ? (
        <p className="text-sm text-gray-500">No tags.</p>
      ) : (
        <div className="overflow-x-auto rounded-md border border-gray-200">
          <table className="w-full text-left text-sm">
            <thead className="bg-gray-50 text-xs text-gray-600">
              <tr>
                <th scope="col" className="px-3 py-2 font-medium">Key</th>
                <th scope="col" className="px-3 py-2 font-medium">Declared here</th>
                <th scope="col" className="px-3 py-2 font-medium">In Unity Catalog</th>
                <th scope="col" className="px-3 py-2 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {keys.map((k) => {
                const exempt = config.exempt_keys.includes(k);
                const assignment = desired?.tag_details.find((t) => t.key === k);
                const entry = driftFor(detail.drift, 'tag', k);
                const inObserved = !!observed && k in observed.tags;
                return (
                  <tr key={k} className="border-t border-gray-100 align-top">
                    <td className="px-3 py-2 font-mono text-xs">
                      <span className="inline-flex items-center gap-1">
                        {exempt && <Lock className="h-3 w-3 text-gray-400" aria-label="Managed outside this tool" />}
                        {k}
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      {assignment ? (
                        <div className="space-y-0.5">
                          <TagValue value={assignment.value} />
                          <AuthorLine author={assignment} />
                        </div>
                      ) : (
                        <span className="text-xs text-gray-400">{exempt ? '—' : 'not declared'}</span>
                      )}
                    </td>
                    <td className="px-3 py-2">{inObserved ? <TagValue value={observed?.tags[k]} /> : <span className="text-xs text-gray-400">not set</span>}</td>
                    <td className="px-3 py-2">{desired ? <SyncCell entry={entry} pending={pending} /> : <span className="text-xs text-gray-400">—</span>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function TagValue({ value }: { value: string | null | undefined }) {
  return value === null || value === undefined || value === '' ? (
    <span className="text-xs italic text-gray-500">key only</span>
  ) : (
    <span className="break-all font-mono text-xs text-gray-900">{value}</span>
  );
}

export function DescriptionSection({ detail }: { detail: ObjectDetail }) {
  const desired = detail.desired?.status === 'active' ? detail.desired : null;
  const observed = detail.observed?.present ? detail.observed : null;
  const entry = driftFor(detail.drift, 'comment');
  return (
    <section>
      <SectionTitle>Description</SectionTitle>
      <div className="space-y-2 text-sm">
        {desired && (
          <div>
            <div className="text-xs font-medium text-gray-500">Declared here</div>
            {desired.comment.value ? <p className="whitespace-pre-wrap text-gray-900">{desired.comment.value}</p> : <p className="text-gray-400">No description</p>}
            <AuthorLine author={desired.comment} />
          </div>
        )}
        {(entry || !desired) && (
          <div>
            <div className="text-xs font-medium text-gray-500">
              In Unity Catalog {entry && <span className="font-medium text-amber-800">· {DRIFT_KIND_LABELS[entry.kind]}</span>}
            </div>
            {observed?.comment ? <p className="whitespace-pre-wrap text-gray-700">{observed.comment}</p> : <p className="text-gray-400">No description</p>}
          </div>
        )}
      </div>
    </section>
  );
}

export function ColumnsSection({ columns, onStage }: { columns: ObjectSummary[]; onStage: (c: ObjectSummary) => void }) {
  const { config, openObject } = useMetadata();
  if (!columns.length) return null;
  return (
    <section>
      <SectionTitle>Columns ({columns.length})</SectionTitle>
      <ul className="divide-y divide-gray-100 rounded-md border border-gray-200">
        {columns.map((c) => {
          const drift = summaryDrift(c, config.exempt_keys).filter((d) => !d.exempt);
          return (
            <li key={c.fqn_key} className="flex flex-wrap items-start gap-2 px-3 py-2">
              <div className="min-w-0 flex-1 space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <button type="button" onClick={() => openObject(c.name, c.column)} className="font-mono text-xs font-medium text-gray-900 hover:text-primary hover:underline">
                    {c.column}
                  </button>
                  {c.data_type && <span className="font-mono text-xs text-gray-400">{c.data_type}</span>}
                  {c.drift && <DriftBadge title={drift.map((d) => `${d.key ?? 'description'}: ${DRIFT_KIND_LABELS[d.kind]}`).join('\n')} />}
                  <StateBadge state={c.object_state} managed={c.managed} />
                </div>
                <TagList tags={displayTags(c)} exemptKeys={config.exempt_keys} emptyText="No tags" />
                {(c.comment ?? c.observed_comment) && <p className="text-xs text-gray-500">{c.managed ? c.comment : c.observed_comment}</p>}
              </div>
              <button
                type="button"
                onClick={() => onStage(c)}
                className="inline-flex h-7 items-center gap-1 rounded px-2 text-xs text-gray-600 hover:bg-gray-100"
                aria-label={`Edit column ${c.column}`}
              >
                <PencilLine className="h-3.5 w-3.5" /> Edit
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export function CounterpartsSection({ counterparts }: { counterparts: Record<string, string> }) {
  const { openObject } = useMetadata();
  const entries = Object.entries(counterparts);
  if (!entries.length) return null;
  return (
    <section>
      <SectionTitle>Same object in other environments</SectionTitle>
      <ul className="flex flex-wrap gap-2">
        {entries.map(([env, name]) => {
          const [base, column] = name.split('::');
          return (
            <li key={env} className="inline-flex items-center gap-1.5">
              <EnvBadge env={env} />
              <button type="button" onClick={() => openObject(base, column ?? null)} className="font-mono text-xs text-primary hover:underline">
                {name.replace('::', '.')}
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** Whether the declared state reached Unity Catalog, and what the reconciler planned last. */
export function ApplySection({ detail }: { detail: ObjectDetail }) {
  const desired = detail.desired?.status === 'active' ? detail.desired : null;
  if (!desired && !detail.recent_plan.length) return null;
  return (
    <section>
      <SectionTitle>Applying to Unity Catalog</SectionTitle>
      {desired && (
        <dl className="grid grid-cols-[auto,1fr] gap-x-4 gap-y-1 text-sm">
          <dt className="text-gray-500">Status</dt>
          <dd>
            <ApplyBadge status={desired.last_apply_status} />
            {!desired.last_apply_status && <span className="text-gray-400">Not applied yet</span>}
          </dd>
          <dt className="text-gray-500">Last applied</dt>
          <dd title={formatTime(desired.last_applied_at)}>{relativeTime(desired.last_applied_at) || '—'}</dd>
          <dt className="text-gray-500">Last checked</dt>
          <dd title={formatTime(desired.last_observed_at)}>{relativeTime(desired.last_observed_at) || '—'}</dd>
          {desired.drift_detected_at && (
            <>
              <dt className="text-gray-500">Drift noticed</dt>
              <dd title={formatTime(desired.drift_detected_at)}>{relativeTime(desired.drift_detected_at)}</dd>
            </>
          )}
          {desired.object_state === 'orphaned' && (
            <>
              <dt className="text-gray-500">Missing since</dt>
              <dd>{formatTime(desired.orphaned_at)}</dd>
            </>
          )}
        </dl>
      )}
      {desired?.last_apply_error && (
        <Notice kind="error" title="The last apply failed" className="mt-2">
          <span className="break-words font-mono text-xs">{desired.last_apply_error}</span>
        </Notice>
      )}
      {detail.recent_plan.length > 0 && (
        <div className="mt-3">
          <div className="mb-1 text-xs font-medium text-gray-500">Recent reconciler statements</div>
          <PlanList items={detail.recent_plan} />
        </div>
      )}
    </section>
  );
}

export function PlanList({ items, showObject }: { items: PlanOutcome[]; showObject?: boolean }) {
  const { openObject } = useMetadata();
  return (
    <ul className="space-y-2">
      {items.map((p, i) => (
        <li key={`${p.fqn_key}-${p.operation}-${i}`} className="rounded-md border border-gray-200 p-2">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <PlanStatusBadge status={p.status} />
            <Badge tone="gray">{p.operation}</Badge>
            {showObject && (
              <button type="button" onClick={() => openObject(p.name, p.column ?? null)} className="font-mono text-primary hover:underline">
                {p.column ? `${p.name}.${p.column}` : p.name}
              </button>
            )}
            {p.removals > 0 && <Badge tone="amber">{p.removals} removal{p.removals === 1 ? '' : 's'}</Badge>}
            {p.operation !== 'comment' && Object.entries(p.tags).map(([k, v]) => <TagChip key={k} tagKey={k} value={v} tone="added" />)}
            {p.keys.map((k) => (
              <TagChip key={k} tagKey={k} value={null} tone="removed" />
            ))}
          </div>
          <div className="mt-1 flex items-start gap-2">
            <code className="block min-w-0 flex-1 whitespace-pre-wrap break-all rounded bg-gray-900 px-2 py-1 font-mono text-xs text-gray-100">{p.sql}</code>
            <CopyButton value={p.sql} label="Copy SQL" />
          </div>
          {p.detail && <p className="mt-1 text-xs text-red-700">{p.detail}</p>}
        </li>
      ))}
    </ul>
  );
}
