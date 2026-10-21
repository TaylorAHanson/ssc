import { useEffect, useMemo, useState } from 'react';
import { AlertCircle, ArrowDown, ArrowUp, ArrowUpDown, Loader2, Lock, RefreshCw, Search, X } from 'lucide-react';
import { api } from '../../services/api';
import type { TablePreviewCell, TablePreviewResponse } from '../../services/api';

interface DataPreviewProps {
  /** `catalog.schema.table` of the table or view. */
  fqn: string;
  /** Rows are only queried once the preview is first shown. */
  active: boolean;
  /** Column types from Unity Catalog, used when the result set doesn't report them. */
  fallbackTypes?: Record<string, string | null | undefined>;
  onRequestAccess?: () => void;
}

type SortDir = 'asc' | 'desc';
type ValueKind = 'number' | 'date' | 'boolean' | 'text';

interface Loaded {
  forRequest: number;
  data: TablePreviewResponse | null;
  failure: string | null;
}

const NUMERIC_TYPE = /^(tinyint|smallint|int|integer|bigint|long|short|byte|float|double|real|decimal|numeric|dec)\b/i;
const DATE_TYPE = /^(date|timestamp|timestamp_ntz|timestamp_ltz)\b/i;
const NUMERIC_FILTER = /^(>=|<=|!=|>|<|=)\s*(-?\d+(?:\.\d+)?)$/;

function kindOf(type: string | null | undefined, values: TablePreviewCell[]): ValueKind {
  if (type) {
    if (NUMERIC_TYPE.test(type)) return 'number';
    if (DATE_TYPE.test(type)) return 'date';
    if (/^bool/i.test(type)) return 'boolean';
    return 'text';
  }
  const present = values.filter((v) => v !== null && v !== '');
  if (present.length > 0 && present.every((v) => typeof v === 'number' || (typeof v === 'string' && !Number.isNaN(Number(v))))) {
    return 'number';
  }
  return 'text';
}

function displayText(value: TablePreviewCell): string {
  if (value === null) return '';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function sortKey(value: TablePreviewCell, kind: ValueKind): number | string | null {
  if (value === null) return null;
  const text = displayText(value);
  if (kind === 'number') {
    const n = Number(text);
    return Number.isNaN(n) ? text : n;
  }
  if (kind === 'date') {
    const t = Date.parse(text);
    return Number.isNaN(t) ? text : t;
  }
  if (kind === 'boolean') return text.toLowerCase() === 'true' ? 1 : 0;
  return text;
}

const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });

function compareKeys(a: number | string, b: number | string): number {
  if (typeof a === 'number' && typeof b === 'number') return a - b;
  if (typeof a === 'number') return -1;
  if (typeof b === 'number') return 1;
  return collator.compare(a, b);
}

/** Per-column filter: substring match, `null` for empty cells, or `>10`-style comparisons on numbers. */
function matchesFilter(value: TablePreviewCell, filter: string, kind: ValueKind): boolean {
  const f = filter.trim();
  if (!f) return true;
  if (f.toLowerCase() === 'null') return value === null;
  if (value === null) return false;
  const text = displayText(value);
  if (kind === 'number') {
    const m = NUMERIC_FILTER.exec(f);
    if (m) {
      const n = Number(text);
      const target = Number(m[2]);
      if (Number.isNaN(n)) return false;
      switch (m[1]) {
        case '>': return n > target;
        case '>=': return n >= target;
        case '<': return n < target;
        case '<=': return n <= target;
        case '=': return n === target;
        case '!=': return n !== target;
      }
    }
  }
  return text.toLowerCase().includes(f.toLowerCase());
}

function errorCopy(kind: TablePreviewResponse['error_kind']): { title: string; body: string } {
  switch (kind) {
    case 'permission_denied':
      return {
        title: "You don't have access to this data",
        body: "You need SELECT on this table (and USE CATALOG / USE SCHEMA on its parents) to preview its rows. Its columns are still listed on the Columns tab.",
      };
    case 'not_found':
      return {
        title: 'Table not found',
        body: "It may have been dropped or renamed, or you can't see it because you lack USE CATALOG / USE SCHEMA on its parents.",
      };
    case 'broken_dependency':
      return {
        title: "This view can't be queried",
        body: 'A table it reads from no longer exists or was renamed.',
      };
    case 'no_warehouse':
      return {
        title: 'Previews are not set up',
        body: 'No SQL warehouse is configured for this app. Ask a platform admin to set one.',
      };
    case 'no_obo':
      return {
        title: "Couldn't read rows as you",
        body: "Your sign-in wasn't passed through to Databricks, so rows aren't read on your behalf. Reload the page and try again.",
      };
    default:
      return { title: "Couldn't load a preview", body: 'The query failed. Try again in a moment.' };
  }
}

/**
 * Sample rows of a table or view, read as the user on the SQL warehouse, with
 * client-side search, sort and per-column filters over the rows returned.
 */
export function DataPreview({ fqn, active, fallbackTypes, onRequestAccess }: DataPreviewProps) {
  const [requested, setRequested] = useState(active);
  const [request, setRequest] = useState(0);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [search, setSearch] = useState('');
  const [filters, setFilters] = useState<Record<number, string>>({});
  const [sort, setSort] = useState<{ col: number; dir: SortDir } | null>(null);

  if (active && !requested) setRequested(true);

  useEffect(() => {
    if (!requested) return;
    let cancelled = false;
    api.getTablePreview(fqn).then(
      (data) => {
        if (!cancelled) setLoaded({ forRequest: request, data, failure: null });
      },
      (e: unknown) => {
        if (!cancelled) setLoaded({ forRequest: request, data: null, failure: e instanceof Error ? e.message : String(e) });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [fqn, requested, request]);

  const loading = requested && loaded?.forRequest !== request;
  const data = loaded?.data ?? null;
  const columns = useMemo(() => data?.columns ?? [], [data]);
  const rows = useMemo(() => data?.rows ?? [], [data]);

  const kinds = useMemo(
    () => columns.map((c, i) => kindOf(c.type ?? fallbackTypes?.[c.name], rows.map((r) => r[i] ?? null))),
    [columns, rows, fallbackTypes],
  );

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const activeFilters = Object.entries(filters).filter(([, v]) => v.trim());
    const indexed = rows.map((row, index) => ({ row, index }));
    const kept = indexed.filter(({ row }) => {
      if (needle && !row.some((cell) => cell !== null && displayText(cell).toLowerCase().includes(needle))) return false;
      return activeFilters.every(([col, f]) => matchesFilter(row[Number(col)] ?? null, f, kinds[Number(col)]));
    });
    if (!sort) return kept;
    const kind = kinds[sort.col];
    const keyed = kept.map((entry) => ({ entry, key: sortKey(entry.row[sort.col] ?? null, kind) }));
    keyed.sort((a, b) => {
      if (a.key === null && b.key === null) return a.entry.index - b.entry.index;
      if (a.key === null) return 1;
      if (b.key === null) return -1;
      const c = compareKeys(a.key, b.key);
      return (sort.dir === 'asc' ? c : -c) || a.entry.index - b.entry.index;
    });
    return keyed.map((k) => k.entry);
  }, [rows, search, filters, sort, kinds]);

  const hasFilters = search.trim() !== '' || Object.values(filters).some((v) => v.trim());

  const toggleSort = (col: number) => {
    setSort((s) => {
      if (!s || s.col !== col) return { col, dir: 'asc' };
      if (s.dir === 'asc') return { col, dir: 'desc' };
      return null;
    });
  };

  const reload = () => {
    setRequested(true);
    setRequest((n) => n + 1);
  };

  if (!requested) return null;

  if (loading && !data) {
    return (
      <div className="flex items-center gap-2 text-sm text-gray-500 bg-gray-50 border border-gray-100 rounded-lg p-4">
        <Loader2 className="w-4 h-4 animate-spin" /> Querying sample rows as you… this can take a moment while the SQL warehouse starts.
      </div>
    );
  }

  if (!loading && (loaded?.failure || data?.error)) {
    const copy = loaded?.failure ? errorCopy('error') : errorCopy(data?.error_kind ?? 'error');
    const denied = data?.error_kind === 'permission_denied';
    return (
      <div className="flex items-start gap-3 text-sm text-amber-900 bg-amber-50 border border-amber-100 rounded-lg p-4">
        {denied ? (
          <Lock className="w-4 h-4 mt-0.5 shrink-0 text-amber-600" />
        ) : (
          <AlertCircle className="w-4 h-4 mt-0.5 shrink-0 text-amber-600" />
        )}
        <div className="min-w-0 flex-1 space-y-2">
          <p className="font-medium">{copy.title}</p>
          <p className="text-amber-800">{copy.body}</p>
          <details className="text-xs text-amber-700">
            <summary className="cursor-pointer select-none">Error details</summary>
            <pre className="mt-1 whitespace-pre-wrap break-words font-mono">{loaded?.failure || data?.error}</pre>
          </details>
          <div className="flex flex-wrap gap-2 pt-1">
            {denied && onRequestAccess && (
              <button
                type="button"
                onClick={onRequestAccess}
                className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-white hover:opacity-90"
              >
                Request access
              </button>
            )}
            <button
              type="button"
              onClick={reload}
              className="inline-flex items-center gap-1.5 rounded-md border border-amber-200 bg-white px-3 py-1.5 text-xs font-medium text-amber-900 hover:bg-amber-100"
            >
              <RefreshCw className="w-3.5 h-3.5" /> Try again
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (!data) return null;

  return (
    <div className="space-y-3">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[12rem] flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-400" />
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search all columns…"
            aria-label="Search preview rows"
            className="w-full rounded-md border border-gray-200 bg-white py-1.5 pl-8 pr-2 text-xs text-gray-900 placeholder:text-gray-400 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
          />
        </div>
        {hasFilters && (
          <button
            type="button"
            onClick={() => {
              setSearch('');
              setFilters({});
            }}
            className="inline-flex items-center gap-1 rounded-md px-2 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-100"
          >
            <X className="h-3.5 w-3.5" /> Clear filters
          </button>
        )}
        <span className="text-xs text-gray-500 tabular-nums" aria-live="polite">
          {visible.length === rows.length
            ? `${rows.length.toLocaleString()} row${rows.length === 1 ? '' : 's'}`
            : `${visible.length.toLocaleString()} of ${rows.length.toLocaleString()} rows`}
        </span>
        <button
          type="button"
          onClick={reload}
          disabled={loading}
          title="Run the preview query again"
          className="inline-flex items-center gap-1 rounded-md border border-gray-200 bg-white px-2 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-50 disabled:opacity-60"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} /> Refresh
        </button>
      </div>

      <p className="text-[11px] text-gray-500">
        {data.truncated
          ? `Showing the first ${data.limit.toLocaleString()} rows, read as you. Search, sort and filters apply to these rows only.`
          : 'Every row, read as you. Row filters and column masks on the table apply.'}
        {' '}Column filters accept text, <code className="font-mono">null</code>, or comparisons like{' '}
        <code className="font-mono">&gt;100</code> on number columns.
      </p>

      {columns.length === 0 || rows.length === 0 ? (
        <div className="bg-gray-50 border border-gray-200 border-dashed rounded-lg p-4 text-center">
          <p className="text-sm text-gray-500">This {columns.length === 0 ? 'query returned no columns' : 'table has no rows'}.</p>
        </div>
      ) : (
        <div className="max-h-[60vh] overflow-auto rounded-md border border-gray-200 bg-white">
          <table className="min-w-full border-separate border-spacing-0 text-xs">
            <thead className="sticky top-0 z-10 bg-gray-50">
              <tr>
                <th
                  scope="col"
                  className="border-b border-gray-200 px-2 py-1.5 text-right font-medium text-gray-400 tabular-nums"
                >
                  #
                </th>
                {columns.map((c, i) => {
                  const dir = sort?.col === i ? sort.dir : null;
                  return (
                    <th
                      key={`${c.name}-${i}`}
                      scope="col"
                      aria-sort={dir === 'asc' ? 'ascending' : dir === 'desc' ? 'descending' : 'none'}
                      className="border-b border-gray-200 px-2 py-1.5 text-left align-bottom font-medium text-gray-700"
                    >
                      <button
                        type="button"
                        onClick={() => toggleSort(i)}
                        title={`Sort by ${c.name}`}
                        className="group inline-flex max-w-[16rem] items-center gap-1 text-left hover:text-gray-900"
                      >
                        <span className="min-w-0">
                          <span className="block truncate font-mono">{c.name}</span>
                          {(c.type ?? fallbackTypes?.[c.name]) && (
                            <span className="block truncate font-mono text-[10px] font-normal text-gray-400">
                              {(c.type ?? fallbackTypes?.[c.name])?.toLowerCase()}
                            </span>
                          )}
                        </span>
                        {dir === 'asc' ? (
                          <ArrowUp className="h-3 w-3 shrink-0 text-primary" />
                        ) : dir === 'desc' ? (
                          <ArrowDown className="h-3 w-3 shrink-0 text-primary" />
                        ) : (
                          <ArrowUpDown className="h-3 w-3 shrink-0 text-gray-300 group-hover:text-gray-500" />
                        )}
                      </button>
                    </th>
                  );
                })}
              </tr>
              <tr>
                <th className="border-b border-gray-200 px-2 pb-1.5" />
                {columns.map((c, i) => (
                  <th key={`${c.name}-${i}-filter`} className="border-b border-gray-200 px-2 pb-1.5 font-normal">
                    <input
                      type="text"
                      value={filters[i] ?? ''}
                      onChange={(e) => setFilters((f) => ({ ...f, [i]: e.target.value }))}
                      placeholder={kinds[i] === 'number' ? 'e.g. >0' : 'Filter…'}
                      aria-label={`Filter ${c.name}`}
                      className="w-full min-w-[5rem] rounded border border-gray-200 bg-white px-1.5 py-0.5 text-[11px] font-normal text-gray-900 placeholder:text-gray-300 focus:border-primary focus:outline-none"
                    />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {visible.length === 0 ? (
                <tr>
                  <td colSpan={columns.length + 1} className="px-3 py-6 text-center text-gray-500">
                    No rows match your search and filters.
                  </td>
                </tr>
              ) : (
                visible.map(({ row, index }) => (
                  <tr key={index} className="hover:bg-blue-50/40">
                    <td className="border-b border-gray-100 px-2 py-1 text-right text-gray-400 tabular-nums">{index + 1}</td>
                    {columns.map((c, i) => {
                      const value = row[i] ?? null;
                      const text = displayText(value);
                      return (
                        <td
                          key={`${c.name}-${i}`}
                          className={`max-w-[20rem] truncate whitespace-nowrap border-b border-gray-100 px-2 py-1 font-mono ${
                            kinds[i] === 'number' ? 'text-right tabular-nums' : ''
                          } text-gray-800`}
                          title={value === null ? 'null' : text}
                        >
                          {value === null ? <span className="italic text-gray-400">null</span> : text}
                        </td>
                      );
                    })}
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
