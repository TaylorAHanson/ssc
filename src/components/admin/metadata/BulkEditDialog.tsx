import { useId, useState } from 'react';
import { previewChange, submitChange } from '../../../services/metadataApi';
import { RELATION_TYPES, type ApprovalRouting, type ChangeRequest, type SecurableType } from '../../../types/metadata';
import { Input } from '../../ui/input';
import { Textarea } from '../../ui/textarea';
import { Notice } from '../../rbac/ui';
import { isPattern, parseSheet, parseTagList, patternOp, patternProblem } from './bulk';
import { useMetadata } from './context';
import { TYPE_LABELS, envForCatalog, plural, tagProblem } from './format';
import { ReviewDialog } from './ReviewDialog';
import { useReviewFlow } from './useReviewFlow';

type Mode = 'pattern' | 'sheet';
type Action = 'set_tags' | 'unset_tags' | 'set_comment';

const SHEET_EXAMPLE = `name\tdata_owner\tdomain\tclassification
sales_dev.bronze.orders\tsales_eng\tsales\tinternal
sales_dev.bronze.customers\tsales_eng\tsales\tconfidential
sales_dev.silver.*\tsales_eng\tsales\t`;

const ACTIONS: { value: Action; label: string }[] = [
  { value: 'set_tags', label: 'Add or update tags' },
  { value: 'unset_tags', label: 'Remove tags' },
  { value: 'set_comment', label: 'Set the description' },
];

const labelClass = 'mb-1 block text-sm font-medium text-gray-800';
const selectClass = 'h-10 w-full rounded-md border border-gray-300 bg-white px-3 text-sm';

/**
 * Tag many objects at once: every object matching a name pattern, or one row per object pasted
 * from a spreadsheet. Both go through the normal preview, approval routing and submit.
 */
export function BulkEditDialog({ onClose, initialPattern = '' }: { onClose: () => void; initialPattern?: string }) {
  const { config, keys, bumpRefresh } = useMetadata();
  const id = useId();
  const [mode, setMode] = useState<Mode>('pattern');
  const [pattern, setPattern] = useState(initialPattern);
  const [column, setColumn] = useState('');
  const [type, setType] = useState<SecurableType | ''>('');
  const [action, setAction] = useState<Action>('set_tags');
  const [tagText, setTagText] = useState('');
  const [comment, setComment] = useState('');
  const [sheet, setSheet] = useState('');
  const [reason, setReason] = useState('');
  const [routing, setRouting] = useState<ApprovalRouting>('request');
  const [envs, setEnvs] = useState<string[]>([]);
  const flow = useReviewFlow<ChangeRequest>({
    preview: previewChange,
    submit: (r, idempotencyKey) => submitChange(r, { idempotencyKey }),
  });

  const relationLevel = pattern.trim().split('.').length === 3 && !column.trim();
  const tagList = parseTagList(tagText);
  const tagIssue =
    tagList.error ??
    (action === 'set_tags'
      ? Object.entries(tagList.tags).map(([k, v]) => tagProblem(k, v, config, keys)).find(Boolean) ?? null
      : null);
  const patternIssue = pattern.trim() ? patternProblem(pattern, column) : null;
  const parsed = mode === 'sheet' ? parseSheet(sheet) : null;

  const ops = (() => {
    if (mode === 'sheet') return parsed && !parsed.errors.length ? parsed.ops : [];
    if (!pattern.trim() || patternIssue) return [];
    if (action === 'set_comment' ? !comment.trim() : tagIssue || !Object.keys(tagList.tags).length) return [];
    return [patternOp({ pattern, column, types: relationLevel && type ? [type] : [], action, tags: tagList.tags, comment: comment.trim() })];
  })();

  // {sales}.bronze.* already names every environment, so there's nothing to copy to.
  const everyEnvironment = mode === 'pattern' && pattern.trim().startsWith('{');
  const sentEnvs = everyEnvironment ? [] : envs;
  const request = (r = reason, route = routing, environments = sentEnvs): ChangeRequest => ({
    ops,
    reason: r,
    environments,
    on_approval_needed: route,
  });
  const opsKey = (r: ChangeRequest | null) => (r ? JSON.stringify([r.ops, r.environments ?? []]) : '');
  const stale = flow.state.step === 'preview' && opsKey(flow.state.request) !== opsKey(request());
  const catalogPart = pattern.trim().split('.')[0] ?? '';
  const sourceEnv = mode === 'pattern' && catalogPart && !isPattern(catalogPart) ? envForCatalog(config, catalogPart) : null;
  const envPatterns = config.environments.some((e) => e.catalog_pattern);

  const close = () => {
    if (flow.state.step === 'result' && flow.state.result.status !== 'rejected') bumpRefresh();
    onClose();
  };

  const tab = (value: Mode, label: string) => (
    <button
      type="button"
      role="tab"
      aria-selected={mode === value}
      onClick={() => setMode(value)}
      className={`rounded-md px-3 py-1.5 text-sm font-medium ${mode === value ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-600 hover:text-gray-900'}`}
    >
      {label}
    </button>
  );

  return (
    <ReviewDialog
      title="Edit many by name"
      subtitle="Tag many objects in one change. You’ll see how many objects it covers, and exactly what changes, before anything is saved."
      flow={flow}
      reason={reason}
      onReasonChange={setReason}
      routing={routing}
      onRoutingChange={setRouting}
      buildRequest={(r, route) => request(r, route)}
      onClose={close}
      onPreview={() => {
        if (ops.length) flow.start(request());
      }}
      previewDisabled={!ops.length}
      stale={stale}
      environments={
        everyEnvironment
          ? undefined
          : {
              value: envs,
              exclude: sourceEnv ? [sourceEnv] : [],
              onChange: (next) => {
                setEnvs(next);
                if (flow.state.step === 'preview' && ops.length) flow.start(request(reason, routing, next));
              },
            }
      }
      reasonPlaceholder="e.g. Ownership and domain tags agreed in the data council"
    >
      <div className="space-y-3">
        <div role="tablist" aria-label="How to choose objects" className="inline-flex gap-1 rounded-lg bg-gray-100 p-1">
          {tab('pattern', 'Match a pattern')}
          {tab('sheet', 'Paste from a spreadsheet')}
        </div>

        {mode === 'pattern' ? (
          <div className="space-y-3">
            <div>
              <label htmlFor={`${id}-pattern`} className={labelClass}>
                Objects
              </label>
              <Input
                id={`${id}-pattern`}
                data-autofocus
                value={pattern}
                onChange={(e) => setPattern(e.target.value)}
                placeholder="sales_dev.bronze.*"
                className="font-mono"
                aria-invalid={!!patternIssue}
                aria-describedby={`${id}-pattern-help`}
              />
              <p id={`${id}-pattern-help`} className={patternIssue ? 'mt-1 text-xs text-red-700' : 'mt-1 text-xs text-gray-500'}>
                {patternIssue ?? (
                  <>
                    * matches any characters and ? one character, within one part. One part matches catalogs, two schemas, three tables and views. Objects
                    prepared before they exist count too.
                    {envPatterns && (
                      <>
                        {' '}
                        Put the catalog in braces, as in <code>{'{sales}'}.bronze.*</code>, to match it in every environment.
                      </>
                    )}
                  </>
                )}
              </p>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <label htmlFor={`${id}-column`} className={labelClass}>
                  Column <span className="font-normal text-gray-500">(optional)</span>
                </label>
                <Input id={`${id}-column`} value={column} onChange={(e) => setColumn(e.target.value)} placeholder="email or *_email" className="font-mono" />
              </div>
              {relationLevel && (
                <div>
                  <label htmlFor={`${id}-type`} className={labelClass}>
                    Only
                  </label>
                  <select id={`${id}-type`} value={type} onChange={(e) => setType(e.target.value as SecurableType | '')} className={selectClass}>
                    <option value="">Tables and views of any kind</option>
                    {RELATION_TYPES.map((t) => (
                      <option key={t} value={t}>
                        {TYPE_LABELS[t]}
                      </option>
                    ))}
                  </select>
                </div>
              )}
            </div>
            <div>
              <label htmlFor={`${id}-action`} className={labelClass}>
                Change
              </label>
              <select id={`${id}-action`} value={action} onChange={(e) => setAction(e.target.value as Action)} className={selectClass}>
                {ACTIONS.map((a) => (
                  <option key={a.value} value={a.value}>
                    {a.label}
                  </option>
                ))}
              </select>
            </div>
            {action === 'set_comment' ? (
              <div>
                <label htmlFor={`${id}-comment`} className={labelClass}>
                  Description
                </label>
                <Textarea id={`${id}-comment`} rows={2} autoResize value={comment} onChange={(e) => setComment(e.target.value)} className="text-sm" />
              </div>
            ) : (
              <div>
                <label htmlFor={`${id}-tags`} className={labelClass}>
                  {action === 'set_tags' ? 'Tags' : 'Tag keys to remove'}
                </label>
                <Input
                  id={`${id}-tags`}
                  value={tagText}
                  onChange={(e) => setTagText(e.target.value)}
                  placeholder={action === 'set_tags' ? 'data_owner=sales_eng; domain=sales; pii' : 'domain; pii'}
                  className="font-mono"
                  aria-invalid={!!tagIssue}
                  aria-describedby={`${id}-tags-help`}
                />
                <p id={`${id}-tags-help`} className={tagIssue ? 'mt-1 text-xs text-red-700' : 'mt-1 text-xs text-gray-500'}>
                  {tagIssue ??
                    (action === 'set_tags'
                      ? 'Separate tags with ;. A key without = is a key-only tag. Other tags on these objects are left alone.'
                      : 'Separate keys with ;.')}
                </p>
              </div>
            )}
          </div>
        ) : (
          <div className="space-y-2">
            <label htmlFor={`${id}-sheet`} className={labelClass}>
              Rows
            </label>
            <Textarea
              id={`${id}-sheet`}
              data-autofocus
              rows={8}
              value={sheet}
              onChange={(e) => setSheet(e.target.value)}
              placeholder={SHEET_EXAMPLE}
              className="font-mono text-xs"
              spellCheck={false}
            />
            {parsed?.errors.length ? (
              <div className="text-xs text-red-700" role="alert">
                <p className="font-medium">Fix these rows, then preview:</p>
                <ul className="list-disc pl-5">
                  {parsed.errors.map((e) => (
                    <li key={e}>{e}</li>
                  ))}
                </ul>
              </div>
            ) : parsed ? (
              <p className="text-xs text-gray-600">
                {plural(parsed.rows, 'row')} → {plural(parsed.targets, 'object')}
                {parsed.patterns > 0 && ` (${plural(parsed.patterns, 'pattern')}, expanded when you preview)`}
                {parsed.tagKeys.length > 0 && `. Tag columns: ${parsed.tagKeys.join(', ')}`}.
              </p>
            ) : null}
            <details className="text-xs text-gray-600">
              <summary className="cursor-pointer">Sheet format</summary>
              <p className="mt-1">
                Copy the cells from Google Sheets or Excel, header row included, and paste them here (CSV works too). The <code>name</code> (or{' '}
                <code>table</code>) column holds <code>catalog.schema.table</code>, or a pattern such as <code>sales_dev.silver.*</code> (
                <code>{'{sales}'}.silver.*</code> for every environment). Every other column is a tag
                key and each non-empty cell sets that tag; empty cells are skipped. Optional columns: <code>column</code> for a column’s tags,{' '}
                <code>description</code>, and <code>tags</code> for extra tags written as <code>key=value; key</code>.
              </p>
              <pre className="mt-1 overflow-x-auto rounded bg-gray-100 p-2 font-mono">{SHEET_EXAMPLE.replace(/\t/g, '  ')}</pre>
            </details>
          </div>
        )}
        {ops.length > config.max_objects_per_change && (
          <Notice kind="warn">
            One change can cover at most {config.max_objects_per_change.toLocaleString()} objects. Split the rows into smaller pastes.
          </Notice>
        )}
      </div>
    </ReviewDialog>
  );
}
