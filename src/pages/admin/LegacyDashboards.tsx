import { useEffect, useMemo, useState } from 'react';
import { format, parseISO } from 'date-fns';
import { AlertTriangle, ArrowLeftRight, Edit, ExternalLink, Loader2, Plus, Search, Trash2, Upload } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Textarea } from '../../components/ui/textarea';
import {
  createLegacyMapping,
  deleteLegacyMapping,
  getLegacyMappings,
  importLegacyMappings,
  updateLegacyMapping,
} from '../../services/api';
import type { DataAsset, LegacyMapping, LegacyMappingImportResult, LegacyMappingInput, LegacyStatus } from '../../services/api';
import { legacyMappingsResource, useMetricViews } from '../../lib/catalogCache';
import { LegacyStatusBadge } from '../../components/discover/LegacyMappingTable';
import { LEGACY_STATUSES, legacyMatches, prettyMetricViewName } from '../../components/discover/legacyMappings';

const EMPTY: LegacyMappingInput = { dashboard: '', metric_view: '', status: 'Active', owner: '', description: '', url: '' };

const IMPORT_COLUMNS = ['dashboard', 'metric_view', 'status', 'owner', 'description', 'url'] as const;
// Header spellings people are likely to paste from a spreadsheet.
const COLUMN_ALIASES: Record<string, (typeof IMPORT_COLUMNS)[number]> = {
  dashboard: 'dashboard',
  'dashboard (legacy)': 'dashboard',
  'legacy dashboard': 'dashboard',
  name: 'dashboard',
  metric_view: 'metric_view',
  'metric view': 'metric_view',
  metricview: 'metric_view',
  status: 'status',
  owner: 'owner',
  description: 'description',
  url: 'url',
  link: 'url',
};

/** CSV (or tab-separated, as pasted from a spreadsheet) with a header row → one object per row. */
function parseMappingsTable(text: string): { rows: Array<Record<string, string>>; error?: string } {
  const firstLine = text.split(/\r?\n/, 1)[0] ?? '';
  const delimiter = firstLine.includes('\t') ? '\t' : ',';
  const records: string[][] = [];
  let field = '';
  let record: string[] = [];
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') {
        field += '"';
        i++;
      } else if (c === '"') {
        quoted = false;
      } else {
        field += c;
      }
    } else if (c === '"' && field === '') {
      quoted = true;
    } else if (c === delimiter) {
      record.push(field);
      field = '';
    } else if (c === '\n' || c === '\r') {
      if (c === '\r' && text[i + 1] === '\n') i++;
      record.push(field);
      records.push(record);
      record = [];
      field = '';
    } else {
      field += c;
    }
  }
  if (field || record.length) {
    record.push(field);
    records.push(record);
  }
  const nonEmpty = records.filter((r) => r.some((v) => v.trim()));
  if (nonEmpty.length === 0) return { rows: [] };

  const header = nonEmpty[0].map((h) => COLUMN_ALIASES[h.trim().toLowerCase()]);
  if (!header.includes('dashboard') || !header.includes('metric_view')) {
    return { rows: [], error: 'The first row must be a header with at least "dashboard" and "metric_view" columns.' };
  }
  const rows = nonEmpty.slice(1).map((r) => {
    const row: Record<string, string> = {};
    header.forEach((key, i) => {
      if (key) row[key] = (r[i] ?? '').trim();
    });
    return row;
  });
  return { rows };
}

/** The catalog metric view an admin typed: full name, or table name if only one view has it. */
function resolveMetricView(ref: string, views: DataAsset[]): DataAsset | null {
  const r = ref.trim().toLowerCase();
  if (!r) return null;
  const exact = views.find((v) => v.id.toLowerCase() === r);
  if (exact || r.includes('.')) return exact ?? null;
  const byName = views.filter((v) => v.table_name.toLowerCase() === r);
  return byName.length === 1 ? byName[0] : null;
}

function refreshDiscover() {
  // Discover reads mappings from the shared catalog cache.
  legacyMappingsResource.load(true).catch(() => {});
}

export function LegacyDashboards() {
  const { data: metricViews } = useMetricViews();
  const [mappings, setMappings] = useState<LegacyMapping[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [filter, setFilter] = useState('');
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  const [form, setForm] = useState<LegacyMappingInput | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  const [importText, setImportText] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState<LegacyMappingImportResult | null>(null);

  const load = async () => {
    setIsLoading(true);
    try {
      setMappings(await getLegacyMappings());
      setLoadError(null);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : 'Failed to load mappings');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const flash = (text: string) => {
    setMessage({ type: 'success', text });
    setTimeout(() => setMessage(null), 3000);
  };

  const views = metricViews as DataAsset[];
  const resolved = form ? resolveMetricView(form.metric_view, views) : null;
  const shown = useMemo(() => mappings.filter((m) => legacyMatches(m, filter)), [mappings, filter]);
  const parsedImport = useMemo(() => (importText ? parseMappingsTable(importText) : null), [importText]);

  const openForm = (m?: LegacyMapping) => {
    setImportText(null);
    setFormError(null);
    setEditingId(m?.id ?? null);
    setForm(
      m
        ? {
            dashboard: m.dashboard,
            metric_view: m.metric_view_id,
            status: m.status,
            owner: m.owner ?? '',
            description: m.description ?? '',
            url: m.url ?? '',
          }
        : EMPTY,
    );
  };

  const closeForm = () => {
    setForm(null);
    setEditingId(null);
    setFormError(null);
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form) return;
    setSaving(true);
    setFormError(null);
    try {
      if (editingId) {
        await updateLegacyMapping(editingId, form);
        flash(`Saved “${form.dashboard}”.`);
      } else {
        await createLegacyMapping(form);
        flash(`Added “${form.dashboard}”.`);
      }
      closeForm();
      await load();
      refreshDiscover();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : 'Failed to save');
    } finally {
      setSaving(false);
    }
  };

  const remove = async (m: LegacyMapping) => {
    setConfirmDeleteId(null);
    try {
      await deleteLegacyMapping(m.id);
      flash(`Deleted “${m.dashboard}”.`);
      if (editingId === m.id) closeForm();
      await load();
      refreshDiscover();
    } catch (err) {
      setMessage({ type: 'error', text: err instanceof Error ? err.message : 'Failed to delete' });
    }
  };

  const runImport = async () => {
    if (!parsedImport || parsedImport.error || parsedImport.rows.length === 0) return;
    setImporting(true);
    setImportResult(null);
    try {
      const result = await importLegacyMappings(parsedImport.rows);
      setImportResult(result);
      if (result.errors.length === 0) setImportText(null);
      await load();
      refreshDiscover();
    } catch (err) {
      setMessage({ type: 'error', text: err instanceof Error ? err.message : 'Import failed' });
    } finally {
      setImporting(false);
    }
  };

  const set = (patch: Partial<LegacyMappingInput>) => setForm((f) => (f ? { ...f, ...patch } : f));

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h2 className="text-xl font-bold text-gray-900 flex items-center gap-2">
            <ArrowLeftRight className="w-5 h-5" /> Legacy Dashboards
          </h2>
          <p className="text-gray-500 text-sm mt-1 max-w-2xl">
            Map dashboards people used before (e.g. in Tableau) to the metric views that replace them. Users see these
            in Discover under <strong>Legacy Dashboards</strong>, on each metric view, and when they search by the old
            dashboard name. This is a temporary aid for the migration.
          </p>
        </div>
        {!form && importText === null && (
          <div className="flex items-center gap-2 shrink-0">
            <Button
              variant="outline"
              onClick={() => {
                setImportResult(null);
                setImportText('');
              }}
              className="flex items-center gap-2"
            >
              <Upload className="w-4 h-4" /> Import
            </Button>
            <Button onClick={() => openForm()} className="flex items-center gap-2">
              <Plus className="w-4 h-4" /> Add mapping
            </Button>
          </div>
        )}
      </div>

      {message && (
        <div
          className={`p-3 rounded-md text-sm ${
            message.type === 'success'
              ? 'bg-green-50 text-green-800 border border-green-200'
              : 'bg-red-50 text-red-800 border border-red-200'
          }`}
        >
          {message.text}
        </div>
      )}

      {form && (
        <Card className="border-primary/20 shadow-md">
          <CardHeader className="bg-gray-50/50 border-b">
            <CardTitle className="text-lg">{editingId ? 'Edit mapping' : 'Add mapping'}</CardTitle>
          </CardHeader>
          <CardContent className="pt-6">
            <form onSubmit={submit} className="space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="space-y-2">
                  <label className="text-sm font-medium" htmlFor="legacy-dashboard">
                    Legacy dashboard
                  </label>
                  <Input
                    id="legacy-dashboard"
                    value={form.dashboard}
                    onChange={(e) => set({ dashboard: e.target.value })}
                    placeholder="Weekly Demand Review"
                    required
                    autoFocus
                  />
                  <p className="text-[11px] text-gray-500">The name people know it by — this is what they'll search for.</p>
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium" htmlFor="legacy-metric-view">
                    Replaced by metric view
                  </label>
                  <Input
                    id="legacy-metric-view"
                    list="legacy-metric-view-options"
                    value={form.metric_view}
                    onChange={(e) => set({ metric_view: e.target.value })}
                    placeholder="catalog.schema.metric_view"
                    required
                    className="font-mono"
                  />
                  <datalist id="legacy-metric-view-options">
                    {views.map((v) => (
                      <option key={v.id} value={v.id}>
                        {[v.domain, v.subdomain].filter(Boolean).join(' › ')}
                      </option>
                    ))}
                  </datalist>
                  {form.metric_view.trim() === '' ? (
                    <p className="text-[11px] text-gray-500">Start typing to pick from the catalog's metric views.</p>
                  ) : resolved ? (
                    <p className="text-[11px] text-emerald-700">
                      {prettyMetricViewName(resolved.table_name)} ·{' '}
                      {[resolved.domain, resolved.subdomain].filter(Boolean).join(' › ') || 'No domain'}
                    </p>
                  ) : (
                    <p className="text-[11px] text-amber-700">Not a metric view in the catalog.</p>
                  )}
                </div>
                <div className="space-y-2">
                  <span className="text-sm font-medium block">Status</span>
                  <div className="inline-flex items-center gap-1 p-1 bg-slate-100 rounded-lg border border-slate-200" role="radiogroup">
                    {LEGACY_STATUSES.map((s: LegacyStatus) => (
                      <button
                        key={s}
                        type="button"
                        role="radio"
                        aria-checked={form.status === s}
                        onClick={() => set({ status: s })}
                        className={`px-3 py-1 text-xs font-semibold rounded-md transition-all cursor-pointer ${
                          form.status === s ? 'bg-white text-slate-900 shadow-sm border border-slate-200' : 'text-slate-600 hover:text-slate-900'
                        }`}
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium" htmlFor="legacy-owner">
                    Owner <span className="text-gray-400 font-normal">(optional)</span>
                  </label>
                  <Input
                    id="legacy-owner"
                    value={form.owner ?? ''}
                    onChange={(e) => set({ owner: e.target.value })}
                    placeholder="Demand Planning"
                  />
                </div>
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium" htmlFor="legacy-url">
                  Link to legacy dashboard <span className="text-gray-400 font-normal">(optional)</span>
                </label>
                <Input
                  id="legacy-url"
                  type="url"
                  value={form.url ?? ''}
                  onChange={(e) => set({ url: e.target.value })}
                  placeholder="https://tableau.example.com/views/…"
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium" htmlFor="legacy-description">
                  Description <span className="text-gray-400 font-normal">(optional)</span>
                </label>
                <Textarea
                  id="legacy-description"
                  value={form.description ?? ''}
                  onChange={(e) => set({ description: e.target.value })}
                  placeholder="Exec-level demand review with forecast vs actuals"
                  rows={2}
                />
              </div>

              {formError && <p className="text-sm text-red-700">{formError}</p>}

              <div className="flex justify-end gap-2 pt-2">
                <Button type="button" variant="outline" onClick={closeForm}>
                  Cancel
                </Button>
                <Button type="submit" disabled={saving || !resolved}>
                  {saving && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
                  {editingId ? 'Save changes' : 'Add mapping'}
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      )}

      {importText !== null && (
        <Card className="border-primary/20 shadow-md">
          <CardHeader className="bg-gray-50/50 border-b">
            <CardTitle className="text-lg">Import mappings</CardTitle>
          </CardHeader>
          <CardContent className="pt-6 space-y-3">
            <p className="text-sm text-gray-600">
              Paste rows from a spreadsheet or CSV. The first row is the header; <code>dashboard</code> and{' '}
              <code>metric_view</code> are required, the rest are optional. Rows already mapped are skipped.
            </p>
            <pre className="text-[11px] bg-slate-50 border border-slate-200 rounded-md p-2 overflow-x-auto text-slate-600">
              {IMPORT_COLUMNS.join(',')}
              {'\n'}Weekly Demand Review,catalog.schema.metric_demand_planning,Active,FPA,Exec-level demand review,https://…
            </pre>
            <Textarea
              value={importText}
              onChange={(e) => {
                setImportText(e.target.value);
                setImportResult(null);
              }}
              rows={8}
              className="font-mono text-xs"
              placeholder={IMPORT_COLUMNS.join(',')}
              autoFocus
            />
            {parsedImport?.error ? (
              <p className="text-sm text-amber-700">{parsedImport.error}</p>
            ) : parsedImport && parsedImport.rows.length > 0 ? (
              <p className="text-sm text-gray-600">
                {parsedImport.rows.length} {parsedImport.rows.length === 1 ? 'row' : 'rows'} ready to import.
              </p>
            ) : null}

            {importResult && (
              <div
                className={`rounded-md border p-3 text-sm ${
                  importResult.errors.length ? 'bg-amber-50 border-amber-200 text-amber-900' : 'bg-green-50 border-green-200 text-green-800'
                }`}
              >
                <p>
                  Added {importResult.created}
                  {importResult.skipped > 0 && `, skipped ${importResult.skipped} already mapped`}
                  {importResult.errors.length > 0 && `, ${importResult.errors.length} not imported:`}
                  {importResult.errors.length === 0 && '.'}
                </p>
                {importResult.errors.length > 0 && (
                  <ul className="mt-1 list-disc pl-5 space-y-0.5 text-xs">
                    {importResult.errors.slice(0, 20).map((e) => (
                      <li key={e.row}>
                        Row {e.row + 1}: {e.error}
                      </li>
                    ))}
                    {importResult.errors.length > 20 && <li>…and {importResult.errors.length - 20} more</li>}
                  </ul>
                )}
              </div>
            )}

            <div className="flex justify-end gap-2 pt-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  setImportText(null);
                  setImportResult(null);
                }}
              >
                {importResult && importResult.errors.length === 0 ? 'Done' : 'Cancel'}
              </Button>
              <Button
                type="button"
                onClick={runImport}
                disabled={importing || !parsedImport || !!parsedImport.error || parsedImport.rows.length === 0}
              >
                {importing && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
                Import
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {importResult && importText === null && (
        <div className="p-3 rounded-md text-sm bg-green-50 text-green-800 border border-green-200">
          Imported {importResult.created} {importResult.created === 1 ? 'mapping' : 'mappings'}
          {importResult.skipped > 0 && ` (${importResult.skipped} already mapped, skipped)`}.
        </div>
      )}

      <Card>
        <CardContent className="p-0">
          {mappings.length > 0 && (
            <div className="flex items-center justify-between gap-3 p-3 border-b border-gray-100">
              <div className="relative w-full max-w-xs">
                <Search className="w-3.5 h-3.5 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
                <input
                  type="text"
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                  placeholder="Filter by dashboard, owner, metric view…"
                  className="w-full pl-9 pr-3 py-1.5 text-sm bg-gray-50 border border-gray-200 rounded-md focus:bg-white focus:outline-none focus:ring-2 focus:ring-primary/20"
                />
              </div>
              <span className="text-xs text-gray-500 whitespace-nowrap">
                {shown.length === mappings.length ? mappings.length : `${shown.length} of ${mappings.length}`} mappings
              </span>
            </div>
          )}

          {isLoading ? (
            <div className="py-12 flex justify-center">
              <Loader2 className="w-8 h-8 animate-spin text-gray-400" />
            </div>
          ) : loadError ? (
            <div className="py-12 text-center text-red-700 text-sm">{loadError}</div>
          ) : mappings.length === 0 ? (
            <div className="py-12 text-center text-gray-500 text-sm">
              No legacy dashboards mapped yet. Add one, or import a list from a spreadsheet.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm text-left">
                <thead className="bg-gray-50 text-gray-900 font-medium">
                  <tr>
                    <th className="p-3">Legacy dashboard</th>
                    <th className="p-3">Status</th>
                    <th className="p-3">Owner</th>
                    <th className="p-3">Metric view</th>
                    <th className="p-3">Updated</th>
                    <th className="p-3 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {shown.map((m) => (
                    <tr key={m.id} className={`hover:bg-gray-50 align-top ${editingId === m.id ? 'bg-primary/5' : ''}`}>
                      <td className="p-3">
                        <div className="font-medium text-gray-900 flex items-center gap-1.5">
                          {m.dashboard}
                          {m.url && (
                            <a href={m.url} target="_blank" rel="noopener noreferrer" className="text-gray-400 hover:text-gray-600" title={m.url}>
                              <ExternalLink className="w-3 h-3" />
                            </a>
                          )}
                        </div>
                        {m.description && <div className="text-xs text-gray-500 line-clamp-1 max-w-sm">{m.description}</div>}
                      </td>
                      <td className="p-3">
                        <LegacyStatusBadge status={m.status} />
                      </td>
                      <td className="p-3 text-gray-600">{m.owner || <span className="text-gray-300">—</span>}</td>
                      <td className="p-3">
                        <div className="font-mono text-xs text-gray-800" title={m.metric_view_id}>
                          {prettyMetricViewName(m.metric_view)}
                        </div>
                        {m.in_catalog ? (
                          <div className="text-xs text-gray-500">{[m.domain, m.subdomain].filter(Boolean).join(' › ')}</div>
                        ) : (
                          <div className="text-xs text-amber-700 inline-flex items-center gap-1" title={m.metric_view_id}>
                            <AlertTriangle className="w-3 h-3" /> No longer in the catalog — hidden in Discover
                          </div>
                        )}
                      </td>
                      <td className="p-3 text-xs text-gray-500 whitespace-nowrap">
                        {format(parseISO(m.updated_at), 'MMM d, yyyy')}
                        {m.updated_by && <div className="text-gray-400 truncate max-w-[10rem]" title={m.updated_by}>{m.updated_by}</div>}
                      </td>
                      <td className="p-3 text-right whitespace-nowrap">
                        {confirmDeleteId === m.id ? (
                          <div className="inline-flex items-center gap-2 text-xs">
                            <span className="text-gray-600">Delete?</span>
                            <button type="button" onClick={() => remove(m)} className="font-semibold text-red-700 hover:text-red-800">
                              Delete
                            </button>
                            <button type="button" onClick={() => setConfirmDeleteId(null)} className="text-gray-500 hover:text-gray-700">
                              Cancel
                            </button>
                          </div>
                        ) : (
                          <div className="flex items-center justify-end gap-2">
                            <button
                              type="button"
                              onClick={() => openForm(m)}
                              className="p-1 text-gray-400 hover:text-blue-600 transition-colors"
                              title="Edit"
                            >
                              <Edit className="w-4 h-4" />
                            </button>
                            <button
                              type="button"
                              onClick={() => setConfirmDeleteId(m.id)}
                              className="p-1 text-gray-400 hover:text-red-600 transition-colors"
                              title="Delete"
                            >
                              <Trash2 className="w-4 h-4" />
                            </button>
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {shown.length === 0 && (
                <div className="py-8 text-center text-gray-500 text-sm">No mappings match “{filter}”.</div>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
