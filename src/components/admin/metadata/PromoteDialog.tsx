import { useState } from 'react';
import { previewPromote, submitPromote } from '../../../services/metadataApi';
import type { ApprovalRouting, ObjectDetail, PromoteRequest } from '../../../types/metadata';
import { EnvBadge } from './badges';
import { useMetadata } from './context';
import { objectLabel } from './format';
import { ReviewDialog } from './ReviewDialog';
import { useReviewFlow } from './useReviewFlow';

/** Copy one object's declared tags and description to the same object in other environments. */
export function PromoteDialog({ detail, onClose }: { detail: ObjectDetail; onClose: () => void }) {
  const { bumpRefresh, config } = useMetadata();
  const name = [detail.key.catalog, detail.key.schema_name, detail.key.object_name].filter(Boolean).join('.');
  const column = detail.key.column_name ?? null;
  const envs = Object.keys(detail.counterparts);
  const strict = new Set(config.environments.filter((e) => e.approval === 'all').map((e) => e.key));
  const relaxed = envs.filter((e) => !strict.has(e));
  const [toEnvs, setToEnvs] = useState<string[]>((relaxed.length ? relaxed : envs).slice(0, 1));
  const [includeTags, setIncludeTags] = useState(true);
  const [includeComment, setIncludeComment] = useState(true);
  const [includeColumns, setIncludeColumns] = useState(detail.columns.length > 0);
  const [reason, setReason] = useState('');
  const [routing, setRouting] = useState<ApprovalRouting>('request');

  const flow = useReviewFlow<PromoteRequest>({
    preview: previewPromote,
    submit: (request, key) => submitPromote({ ...request, idempotency_key: key }),
  });

  const request = (r = reason, route = routing): PromoteRequest => ({
    name,
    column,
    to_environments: toEnvs,
    include_tags: includeTags,
    include_comment: includeComment,
    include_columns: includeColumns,
    reason: r,
    on_approval_needed: route,
  });
  const optionsKey = (r: PromoteRequest) => JSON.stringify([r.to_environments, r.include_tags, r.include_comment, r.include_columns]);
  const stale = flow.state.step === 'preview' && optionsKey(flow.state.request) !== optionsKey(request());
  const nothing = !toEnvs.length || (!includeTags && !includeComment);

  const close = () => {
    if (flow.state.step === 'result' && flow.state.result.status !== 'rejected') bumpRefresh();
    onClose();
  };

  return (
    <ReviewDialog
      title="Copy to another environment"
      subtitle={<span className="font-mono">{objectLabel(name, column)}</span>}
      flow={flow}
      reason={reason}
      onReasonChange={setReason}
      routing={routing}
      onRoutingChange={setRouting}
      buildRequest={(r, route) => request(r, route)}
      onClose={close}
      onPreview={() => flow.start(request())}
      previewDisabled={nothing}
      stale={stale}
    >
      <div className="space-y-3 rounded-md border border-gray-200 p-3">
        <fieldset>
          <legend className="mb-1 text-sm font-medium text-gray-900">Copy to</legend>
          <div className="space-y-1">
            {envs.map((env) => (
              <label key={env} className="flex cursor-pointer items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={toEnvs.includes(env)}
                  onChange={(e) => setToEnvs(e.target.checked ? [...toEnvs, env] : toEnvs.filter((x) => x !== env))}
                  className="h-3.5 w-3.5 rounded border-gray-300 text-primary focus:ring-primary"
                />
                <EnvBadge env={env} />
                <span className="font-mono text-xs text-gray-600">{detail.counterparts[env].replace('::', '.')}</span>
              </label>
            ))}
          </div>
        </fieldset>
        <fieldset className="flex flex-wrap gap-4 text-sm">
          <legend className="mb-1 text-sm font-medium text-gray-900">What to copy</legend>
          <label className="inline-flex cursor-pointer items-center gap-1.5">
            <input type="checkbox" checked={includeTags} onChange={(e) => setIncludeTags(e.target.checked)} className="h-3.5 w-3.5 rounded border-gray-300" />
            Tags
          </label>
          <label className="inline-flex cursor-pointer items-center gap-1.5">
            <input type="checkbox" checked={includeComment} onChange={(e) => setIncludeComment(e.target.checked)} className="h-3.5 w-3.5 rounded border-gray-300" />
            Description
          </label>
          {detail.columns.length > 0 && (
            <label className="inline-flex cursor-pointer items-center gap-1.5">
              <input type="checkbox" checked={includeColumns} onChange={(e) => setIncludeColumns(e.target.checked)} className="h-3.5 w-3.5 rounded border-gray-300" />
              Columns too ({detail.columns.length})
            </label>
          )}
        </fieldset>
        <p className="text-xs text-gray-500">
          The target’s tags are replaced with this object’s, so tags it has that this one doesn’t are removed. The preview shows exactly what changes.
        </p>
      </div>
    </ReviewDialog>
  );
}
