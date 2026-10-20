import { useMemo } from 'react';
import { renderMarkdownSafe } from '../../../lib/markdown';
import type { ChangePreview, Finding, ItemDecision } from '../../../types/metadata';
import { Badge, Notice } from '../../rbac/ui';
import { EnvBadge, OutcomeBadge } from './badges';
import { VIA_LABELS, describeItem, objectLabel, outcomeOf, plural } from './format';

const MAX_ITEMS = 200;

function FindingList({ findings }: { findings: Finding[] }) {
  if (!findings.length) return null;
  return (
    <ul className="space-y-1">
      {findings.map((f, i) => (
        <li key={`${f.code}-${f.fqn_key ?? ''}-${f.key ?? ''}-${i}`} className="flex items-start gap-2 text-sm">
          <Badge tone={f.severity === 'deny' ? 'red' : f.severity === 'warn' ? 'amber' : 'blue'}>
            {f.severity === 'deny' ? 'Blocks' : f.severity === 'warn' ? 'Warning' : 'Note'}
          </Badge>
          <span className="min-w-0 break-words text-gray-800">{f.message}</span>
        </li>
      ))}
    </ul>
  );
}

/** What a change would do: per-item decisions, findings, approvers and the approvers' report. */
export function PreviewView({ preview, reasonOk }: { preview: ChangePreview; reasonOk: boolean }) {
  const findings = preview.findings.filter((f) => !(reasonOk && f.code === 'reason_short'));
  const blocking = findings.filter((f) => f.severity === 'deny');
  const other = findings.filter((f) => f.severity !== 'deny');
  const grouped = useMemo(() => {
    const map = new Map<string, ItemDecision[]>();
    preview.items.slice(0, MAX_ITEMS).forEach((d) => map.set(d.item.fqn_key, [...(map.get(d.item.fqn_key) ?? []), d]));
    return [...map.values()];
  }, [preview.items]);
  const reportHtml = useMemo(() => (preview.report_markdown ? renderMarkdownSafe(preview.report_markdown) : ''), [preview.report_markdown]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-1.5 text-sm">
        <span className="font-medium text-gray-900">{plural(preview.object_count, 'object')}:</span>
        {preview.direct_count > 0 && <Badge tone="green">{plural(preview.direct_count, 'edit')} saved directly</Badge>}
        {preview.approval_count > 0 && <Badge tone="amber">{plural(preview.approval_count, 'edit')} need approval</Badge>}
        {preview.denied_count > 0 && <Badge tone="red">{plural(preview.denied_count, 'edit')} not allowed</Badge>}
        {preview.noop_count > 0 && (
          <Badge tone="gray" title="These already have the value you chose, so they’re skipped.">
            {plural(preview.noop_count, 'edit')} already in place
          </Badge>
        )}
        {preview.environments.length > 0 && (
          <span className="inline-flex items-center gap-1 text-xs text-gray-500">
            Environments: {preview.environments.map((e) => <EnvBadge key={e} env={e} />)}
          </span>
        )}
      </div>

      {preview.blocked && (
        <Notice kind="error" title="This change can’t be submitted yet">
          {blocking.length ? <FindingList findings={blocking} /> : 'Some edits aren’t allowed. Remove them and preview again.'}
        </Notice>
      )}
      {!preview.blocked && blocking.length > 0 && <FindingList findings={blocking} />}
      {other.length > 0 && <FindingList findings={other} />}

      {preview.approver_groups.length > 0 && (
        <p className="text-sm text-gray-700">
          Approval goes to <span className="font-medium">{preview.approver_groups.join(', ')}</span>.
        </p>
      )}

      {preview.items.length === 0 ? (
        <p className="text-sm text-gray-500">Nothing would change: everything already matches.</p>
      ) : (
        <ul className="divide-y divide-gray-100 rounded-md border border-gray-200">
          {grouped.map((decisions) => {
            const first = decisions[0].item;
            return (
              <li key={first.fqn_key} className="space-y-1.5 px-3 py-2">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="font-mono text-xs font-medium text-gray-900">{objectLabel(first.name, first.column)}</span>
                  <EnvBadge env={first.environment} />
                  {!first.object_exists && <Badge tone="blue">Not created yet</Badge>}
                  {first.copied_from && <span className="text-xs text-gray-500">copied from {first.copied_from}</span>}
                </div>
                <ul className="space-y-1.5">
                  {decisions.map((d, i) => (
                    <DecisionRow key={`${d.item.field}-${d.item.key ?? ''}-${i}`} decision={d} />
                  ))}
                </ul>
              </li>
            );
          })}
        </ul>
      )}
      {preview.items.length > MAX_ITEMS && (
        <p className="text-xs text-gray-500">
          Showing the first {MAX_ITEMS} of {preview.items.length.toLocaleString()} edits. The report below covers all of them.
        </p>
      )}

      {reportHtml && (
        <details className="rounded-md border border-gray-200">
          <summary className="cursor-pointer px-3 py-2 text-sm font-medium text-gray-700">What approvers will see</summary>
          <div
            className="prose prose-sm agent-prose max-w-none overflow-x-auto border-t border-gray-100 px-3 py-3"
            dangerouslySetInnerHTML={{ __html: reportHtml }}
          />
        </details>
      )}
    </div>
  );
}

function DecisionRow({ decision }: { decision: ItemDecision }) {
  const outcome = outcomeOf(decision);
  const { decision: d } = decision;
  const why = outcome === 'direct' ? [VIA_LABELS[d.via] ? `You can make this ${VIA_LABELS[d.via]}.` : ''] : [...d.sensitive_reasons, ...d.reasons];
  return (
    <li className="text-sm">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <span className="min-w-0 break-words font-mono text-xs text-gray-800">{describeItem(decision.item)}</span>
        <OutcomeBadge outcome={outcome} />
      </div>
      {why.filter(Boolean).length > 0 && (
        <ul className="mt-0.5 list-disc pl-5 text-xs text-gray-600">
          {why.filter(Boolean).map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      )}
      {outcome === 'approval' && d.approver_groups.length > 0 && (
        <p className="text-xs text-gray-600">
          Approvers: {d.approver_groups.join(', ')}
          {d.owner_source && ` (owners from ${d.owner_source})`}
        </p>
      )}
      {decision.findings.length > 0 && (
        <div className="mt-1">
          <FindingList findings={decision.findings} />
        </div>
      )}
    </li>
  );
}
