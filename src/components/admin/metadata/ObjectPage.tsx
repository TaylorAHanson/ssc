import { useCallback, useEffect, useState } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { ArrowUpRight, ChevronDown, Loader2, RefreshCw, Sparkles } from 'lucide-react';
import { getObject, MetadataApiError, notifyReconcile } from '../../../services/metadataApi';
import type { ObjectDetail, ObjectSummary } from '../../../types/metadata';
import { Button } from '../../ui/button';
import { Input } from '../../ui/input';
import { Badge, EmptyState, ErrorBanner, Notice, SectionTitle, Skeleton } from '../../rbac/ui';
import { AI_NOTE, editsCatalog, outcomeMessage, suggestDescriptions, type SuggestOptions } from './aiSuggest';
import { AttentionBadge } from './badges';
import { useMetadata } from './context';
import { MenuItem, Popover } from './Popover';
import { EventList } from './EventList';
import { plural, reportOnlyNote, syncsObject } from './format';
import { errorMessage } from '../../../lib/errors';
import { DescriptionField, TagsField } from './InlineEdit';
import { Breadcrumb, ObjectHeader } from './ObjectHeader';
import { ApplySection, CounterpartsSection, DescriptionSection, DesiredVsActual } from './ObjectSections';
import { PromoteDialog } from './PromoteDialog';
import { Rollup } from './Rollup';
import { effectiveComment, hasEdits, seedFromSummary, useStagingStore } from './staging';
import { useAsync } from './useAsync';

interface AiControls {
  /** Every column with no description, counting unsaved edits. */
  generateEmpty: () => void;
  regenerateAll: () => void;
  column: (name: string) => void;
  busy: boolean;
  error: string | null;
}

/** AI suggest actions for one table, or null when they're off or this deployment doesn't take edits here. */
function useAiControls(detail: ObjectDetail): (AiControls & { table: () => void }) | null {
  const { config, notify } = useMetadata();
  const [running, setRunning] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const run = async (opts: SuggestOptions, bulk: boolean) => {
    if (bulk) {
      setError(null);
      setRunning((n) => n + 1);
    }
    try {
      const outcome = await suggestDescriptions(detail, opts);
      if (bulk || !outcome.staged) notify(outcomeMessage(outcome), outcome.staged ? { kind: 'success' } : undefined);
    } catch (e) {
      const message = errorMessage(e, 'Couldn’t get AI suggestions. Try again in a moment.');
      if (bulk) setError(message);
      else notify(message, { kind: 'error' });
    } finally {
      if (bulk) setRunning((n) => n - 1);
    }
  };

  if (!config.ai_descriptions_enabled || !editsCatalog(config, detail.key.catalog)) return null;
  return {
    busy: running > 0,
    error,
    generateEmpty: () => {
      const targets = useStagingStore.getState().targets;
      const empty = detail.columns.filter((c) => {
        const seed = seedFromSummary(c);
        const t = targets[seed.key];
        return c.column && !(t ? effectiveComment(t) : seed.baseComment)?.trim();
      });
      if (!empty.length) {
        notify('Every column already has a description. Use Regenerate all to replace them with new suggestions.');
        return;
      }
      void run({ mode: 'empty_only', columns: empty.map((c) => c.column as string) }, true);
    },
    regenerateAll: () => void run({ mode: 'all' }, true),
    column: (name) => void run({ mode: 'all', columns: [name], replaceTyped: true }, false),
    table: () => void run({ mode: 'all', columns: [], includeTable: true, replaceTyped: true }, false),
  };
}

/** One table or view on its own page: edit its description, tags and columns in place. */
export function ObjectPage() {
  const { catalog = '', schema = '', table = '' } = useParams<{ catalog: string; schema: string; table: string }>();
  const [params] = useSearchParams();
  const focusColumn = params.get('column');
  const name = `${catalog}.${schema}.${table}`;
  const { refreshToken } = useMetadata();
  const load = useCallback(() => getObject(name), [name]);
  const { data, error, loading, reload } = useAsync(load, { token: refreshToken });
  const notFound = error instanceof MetadataApiError && error.status === 404;

  return (
    <div className="space-y-4">
      <Breadcrumb catalog={catalog} schema={schema} table={table} />
      {notFound ? (
        <EmptyState title="We couldn’t find this table">
          <span className="font-mono">{name}</span> isn’t in Unity Catalog, or it hasn’t been read yet. Check the name, or go back and search for it.
        </EmptyState>
      ) : error ? (
        <ErrorBanner message={errorMessage(error, 'Couldn’t load this table')} onRetry={reload} />
      ) : null}
      {!data && loading && <Skeleton rows={6} />}
      {data && <ObjectBody detail={data} table={table} focusColumn={focusColumn} reload={reload} />}
    </div>
  );
}

function ObjectBody({ detail, table, focusColumn, reload }: { detail: ObjectDetail; table: string; focusColumn: string | null; reload: () => void }) {
  const { config, notify } = useMetadata();
  const [promote, setPromote] = useState(false);
  const [checking, setChecking] = useState(false);
  const desired = detail.desired?.status === 'active' ? detail.desired : null;
  const outOfSync = detail.drift.filter((d) => !d.exempt).length;
  const ai = useAiControls(detail);
  const hasAiSuggestions = useStagingStore((s) =>
    Object.values(s.targets).some((t) => !!t.comment?.ai && (t.key === detail.fqn_key || t.key.startsWith(`${detail.fqn_key}::`))),
  );

  const checkNow = async () => {
    setChecking(true);
    try {
      const res = await notifyReconcile({ names: [detail.fqn_key] });
      notify(res.enqueued.length ? 'Checking Unity Catalog now. This page refreshes in a few seconds.' : 'Couldn’t start a check for this table.', {
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
    <>
      <ObjectHeader detail={detail} title={table} onSuggestDescription={ai?.table} />

      <div className="text-sm text-gray-600">
        {detail.editable ? 'You can change this directly. Your changes are saved when you select Review and save.' : 'Changes you make here are sent to the owners for approval before they’re saved.'}
        {detail.edit_reasons.length > 0 && (
          <details className="mt-1 inline-block text-xs text-gray-500 [&_summary]:cursor-pointer">
            <summary className="ml-1 inline underline">Why?</summary>
            <ul className="mt-1 list-disc pl-5">
              {detail.edit_reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          </details>
        )}
      </div>

      {desired?.last_apply_status === 'failed' && (
        <Notice kind="error" title="Couldn’t update Unity Catalog">
          The last attempt to write this table’s tags failed{desired.last_apply_error ? ':' : '.'}
          {desired.last_apply_error && <span className="mt-1 block break-words font-mono text-xs">{desired.last_apply_error}</span>}
        </Notice>
      )}
      {outOfSync > 0 && (
        <Notice kind="warn" title="Out of sync with Unity Catalog">
          Unity Catalog has {plural(outOfSync, 'different value')} for this table.
          {syncsObject(config, detail.fqn_key)
            ? ' Someone may have changed it there directly, or a save hasn’t reached it yet.'
            : ` This won’t be written to Unity Catalog. ${reportOnlyNote(config)} Change this under Admin → Settings.`}
          <details className="mt-2 [&_summary]:cursor-pointer">
            <summary className="font-medium underline">Compare</summary>
            <div className="mt-2 space-y-4 rounded-md bg-white p-3 text-gray-900">
              <DesiredVsActual detail={detail} />
              <DescriptionSection detail={detail} />
            </div>
          </details>
        </Notice>
      )}

      {hasAiSuggestions && (
        <Notice kind="info" className="py-2">
          {AI_NOTE}
        </Notice>
      )}

      <ColumnsGrid columns={detail.columns} focusColumn={focusColumn} ai={ai} />

      <Rollup title="Details" hint={detailsHint(detail)}>
        <section>
          <SectionTitle>Owners</SectionTitle>
          {detail.owner_groups.length ? (
            <p className="flex flex-wrap items-center gap-1.5 text-sm">
              {detail.owner_groups.map((g) => (
                <Badge key={g} tone="indigo">
                  {g}
                </Badge>
              ))}
              {detail.owner_source && <span className="text-xs text-gray-500">from the {config.owner_tag_key} tag on {detail.owner_source}</span>}
            </p>
          ) : (
            <p className="text-sm text-gray-500">
              No owners yet. Add a <code>{config.owner_tag_key}</code> tag here or on the schema or catalog, and its owners can change things without an admin.
            </p>
          )}
        </section>
        <CounterpartsSection counterparts={detail.counterparts} />
        <ApplySection detail={detail} />
        <div className="flex flex-wrap gap-2">
          {desired && config.reconcile_mode !== 'off' && (
            <Button size="sm" variant="outline" className="gap-1.5" onClick={checkNow} disabled={checking}>
              <RefreshCw className="h-3.5 w-3.5" /> {checking ? 'Checking…' : 'Check Unity Catalog now'}
            </Button>
          )}
          {desired && Object.keys(detail.counterparts).length > 0 && (
            <Button size="sm" variant="outline" className="gap-1.5" onClick={() => setPromote(true)}>
              <ArrowUpRight className="h-3.5 w-3.5" /> Copy to another environment
            </Button>
          )}
        </div>
      </Rollup>

      <Rollup title="Activity" hint={detail.history.length ? plural(detail.history.length, 'change') : 'No changes yet'}>
        <EventList events={detail.history} empty="No changes have been made to this table here yet." />
      </Rollup>

      {promote && <PromoteDialog detail={detail} onClose={() => setPromote(false)} />}
    </>
  );
}

/** What the Details rollup holds for this table, matching the sections it shows. */
function detailsHint(detail: ObjectDetail): string {
  const parts = ['Owners'];
  if (Object.keys(detail.counterparts).length) parts.push('other environments');
  if (detail.desired?.status === 'active' || detail.recent_plan.length) parts.push('sync status');
  return parts.length > 1 ? `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}` : parts[0];
}

function ColumnsGrid({ columns, focusColumn, ai }: { columns: ObjectSummary[]; focusColumn: string | null; ai: AiControls | null }) {
  const [filter, setFilter] = useState('');
  const term = filter.trim().toLowerCase();
  const shown = term
    ? columns.filter((c) => (c.column ?? '').toLowerCase().includes(term) || (c.comment ?? c.observed_comment ?? '').toLowerCase().includes(term))
    : columns;

  useEffect(() => {
    if (!focusColumn) return;
    document.getElementById(`column-${focusColumn.toLowerCase()}`)?.scrollIntoView({ block: 'center' });
  }, [focusColumn]);

  if (!columns.length) return null;
  return (
    <section className="space-y-2">
      <div className="flex flex-wrap items-center gap-3">
        <h4 className="text-sm font-semibold text-gray-900">Columns ({columns.length})</h4>
        {columns.length > 12 && (
          <Input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Find a column" aria-label="Find a column" className="h-8 w-56 text-sm" />
        )}
        {ai && (
          <div className="ml-auto flex items-center">
            <Button
              size="sm"
              variant="outline"
              className="h-8 gap-1.5 rounded-r-none"
              onClick={ai.generateEmpty}
              disabled={ai.busy}
              title="Suggest descriptions for columns that don’t have one. Suggestions are added as unsaved changes for you to review."
            >
              {ai.busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
              {ai.busy ? 'Generating…' : 'AI generate'}
            </Button>
            <Popover
              label={<ChevronDown className="h-3.5 w-3.5" />}
              ariaLabel="More AI generate options"
              align="right"
              buttonClassName="h-8 rounded-l-none border-l-0 px-2"
            >
              {(close) => (
                <MenuItem
                  onClick={() => {
                    close();
                    if (!ai.busy) ai.regenerateAll();
                  }}
                  hint="New suggestions for every column, including ones that already have a description. Nothing is saved until you review it."
                >
                  Regenerate all
                </MenuItem>
              )}
            </Popover>
          </div>
        )}
      </div>
      {ai?.error && (
        <Notice kind="error" title="Couldn’t get AI suggestions">
          {ai.error}
        </Notice>
      )}
      <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
        <table className="w-full min-w-[40rem] table-fixed text-left text-sm">
          <thead className="bg-gray-50 text-xs text-gray-600">
            <tr>
              <th scope="col" className="w-56 px-3 py-2 font-medium">Column</th>
              <th scope="col" className="w-[45%] px-3 py-2 font-medium">Description</th>
              <th scope="col" className="px-3 py-2 font-medium">Tags</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((c) => (
              <ColumnRow
                key={c.fqn_key}
                column={c}
                focused={!!focusColumn && c.column?.toLowerCase() === focusColumn.toLowerCase()}
                onSuggest={ai && c.column ? () => ai.column(c.column as string) : undefined}
              />
            ))}
          </tbody>
        </table>
      </div>
      {term && shown.length === 0 && <p className="text-sm text-gray-500">No column matches “{filter}”.</p>}
    </section>
  );
}

function ColumnRow({ column: c, focused, onSuggest }: { column: ObjectSummary; focused: boolean; onSuggest?: () => void }) {
  const seed = seedFromSummary(c);
  const unsaved = useStagingStore((s) => !!s.targets[seed.key] && hasEdits(s.targets[seed.key]));
  return (
    <tr id={`column-${(c.column ?? '').toLowerCase()}`} className={focused ? 'border-t border-gray-100 bg-amber-50/60 align-top' : 'border-t border-gray-100 align-top'}>
      <td className="px-3 py-2.5">
        <div className="break-all font-mono text-xs font-medium text-gray-900">{c.column}</div>
        {c.data_type && <div className="font-mono text-xs text-gray-400">{c.data_type.toLowerCase()}</div>}
        <div className="mt-1">
          <AttentionBadge summary={c} unsaved={unsaved} />
        </div>
      </td>
      <td className="px-3 py-2.5">
        <DescriptionField seed={seed} compact onSuggest={onSuggest} />
      </td>
      <td className="px-3 py-2.5">
        <TagsField seed={seed} compact />
      </td>
    </tr>
  );
}
