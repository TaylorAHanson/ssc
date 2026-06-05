import { useEffect, useMemo, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import {
  Tags,
  Plus,
  Trash2,
  Loader2,
  GitPullRequest,
  ExternalLink,
  RefreshCw,
  AlertCircle,
  Info,
  CheckCircle2,
} from 'lucide-react';
import { api } from '../../services/api';
import type { TagDataset, TableTags, TagChange } from '../../services/api';
import { format, parseISO } from 'date-fns';

const parseUtc = (value: string): Date =>
  parseISO(/Z|[+-]\d{2}:?\d{2}$/.test(value) ? value : `${value}Z`);

interface TagRow {
  key: string;
  value: string;
}

// Map the backend request status to a friendly label + style for the
// "Recent tag changes" list.
function statusBadge(status: string): { label: string; className: string } {
  switch (status) {
    case 'completed':
      return { label: 'Applied', className: 'bg-green-100 text-green-800' };
    case 'provisioning':
      return { label: 'PR Open', className: 'bg-blue-100 text-blue-800' };
    case 'rejected':
      return { label: 'Closed / Rejected', className: 'bg-gray-200 text-gray-700' };
    case 'failed':
      return { label: 'Failed', className: 'bg-red-100 text-red-800' };
    default:
      return { label: 'Queued', className: 'bg-amber-100 text-amber-800' };
  }
}

function buildDesired(rows: TagRow[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const r of rows) {
    const k = r.key.trim();
    if (k) out[k] = r.value;
  }
  return out;
}

export function TagManagement() {
  const [datasets, setDatasets] = useState<TagDataset[]>([]);
  const [isLoadingDatasets, setIsLoadingDatasets] = useState(true);
  const [selectedDataset, setSelectedDataset] = useState<string>('');

  const [tables, setTables] = useState<TableTags[]>([]);
  const [suggestedKeys, setSuggestedKeys] = useState<string[]>([]);
  const [original, setOriginal] = useState<Record<string, Record<string, string>>>({});
  const [edited, setEdited] = useState<Record<string, TagRow[]>>({});
  const [isLoadingTables, setIsLoadingTables] = useState(false);
  const [tablesError, setTablesError] = useState<string | null>(null);

  const [batchRows, setBatchRows] = useState<TagRow[]>([]);

  const [isSubmitting, setIsSubmitting] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  const [changes, setChanges] = useState<TagChange[]>([]);
  const [isLoadingChanges, setIsLoadingChanges] = useState(true);

  // ---------------------------------------------------------------- load data

  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const ds = await api.getTagDatasets();
        if (mounted) setDatasets(ds);
      } catch (e) {
        console.error('Failed to load tag datasets', e);
      } finally {
        if (mounted) setIsLoadingDatasets(false);
      }
    })();
    return () => {
      mounted = false;
    };
  }, []);

  const loadChanges = async () => {
    setIsLoadingChanges(true);
    try {
      setChanges(await api.listTagChanges());
    } catch (e) {
      console.error('Failed to load tag changes', e);
    } finally {
      setIsLoadingChanges(false);
    }
  };

  useEffect(() => {
    loadChanges();
  }, []);

  const loadDatasetTables = async (datasetId: string) => {
    setIsLoadingTables(true);
    setTablesError(null);
    setTables([]);
    setOriginal({});
    setEdited({});
    try {
      const resp = await api.getDatasetTags(datasetId);
      setTables(resp.tables);
      setSuggestedKeys(resp.suggested_keys || []);
      if (resp.error) setTablesError(resp.error);

      const orig: Record<string, Record<string, string>> = {};
      const ed: Record<string, TagRow[]> = {};
      for (const t of resp.tables) {
        const tagMap: Record<string, string> = {};
        const rows: TagRow[] = [];
        for (const [k, v] of Object.entries(t.tags)) {
          tagMap[k] = v ?? '';
          rows.push({ key: k, value: v ?? '' });
        }
        orig[t.table] = tagMap;
        ed[t.table] = rows;
      }
      setOriginal(orig);
      setEdited(ed);
    } catch (e: any) {
      setTablesError(e?.message || 'Failed to load tables');
    } finally {
      setIsLoadingTables(false);
    }
  };

  const handleSelectDataset = (datasetId: string) => {
    setSelectedDataset(datasetId);
    setMessage(null);
    if (datasetId) loadDatasetTables(datasetId);
  };

  // ---------------------------------------------------------------- edit rows

  const updateRow = (table: string, idx: number, field: 'key' | 'value', value: string) => {
    setEdited((prev) => {
      const rows = [...(prev[table] || [])];
      rows[idx] = { ...rows[idx], [field]: value };
      return { ...prev, [table]: rows };
    });
  };

  const addRow = (table: string) => {
    setEdited((prev) => ({ ...prev, [table]: [...(prev[table] || []), { key: '', value: '' }] }));
  };

  const removeRow = (table: string, idx: number) => {
    setEdited((prev) => {
      const rows = [...(prev[table] || [])];
      rows.splice(idx, 1);
      return { ...prev, [table]: rows };
    });
  };

  // ----------------------------------------------------------------- batch

  const applyBatchToAll = () => {
    const apply = buildDesired(batchRows);
    if (Object.keys(apply).length === 0) return;
    setEdited((prev) => {
      const next = { ...prev };
      for (const t of tables) {
        const rows = [...(next[t.table] || [])];
        for (const [k, v] of Object.entries(apply)) {
          const existing = rows.findIndex((r) => r.key.trim() === k);
          if (existing >= 0) rows[existing] = { key: k, value: v };
          else rows.push({ key: k, value: v });
        }
        next[t.table] = rows;
      }
      return next;
    });
  };

  // ------------------------------------------------------------------- diff

  const diffTable = (table: string) => {
    const desired = buildDesired(edited[table] || []);
    const orig = original[table] || {};
    const setTags: Record<string, string> = {};
    const unsetTags: string[] = [];
    for (const k of Object.keys(desired)) if (orig[k] !== desired[k]) setTags[k] = desired[k];
    for (const k of Object.keys(orig)) if (!(k in desired)) unsetTags.push(k);
    return {
      desired,
      set: setTags,
      unset: unsetTags,
      changed: Object.keys(setTags).length > 0 || unsetTags.length > 0,
    };
  };

  const changedTables = useMemo(
    () => tables.filter((t) => diffTable(t.table).changed),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [tables, edited, original]
  );

  // ----------------------------------------------------------------- submit

  const handleSubmit = async () => {
    if (changedTables.length === 0) return;
    setIsSubmitting(true);
    setMessage(null);
    try {
      const payloadTables = changedTables.map((t) => ({
        table: t.table,
        desired_tags: diffTable(t.table).desired,
      }));
      const result = await api.createTagChange({
        dataset_id: selectedDataset,
        dataset_name: selectedDataset,
        tables: payloadTables,
      });
      setMessage({
        type: 'success',
        text: `Tag change submitted (${result.table_count} table${result.table_count === 1 ? '' : 's'}). A pull request will open shortly for governance review.`,
      });
      await loadChanges();
      // Reset the editor baseline so the same change can't be submitted twice.
      if (selectedDataset) await loadDatasetTables(selectedDataset);
    } catch (e: any) {
      setMessage({ type: 'error', text: e?.message || 'Failed to submit tag change' });
    } finally {
      setIsSubmitting(false);
    }
  };

  // ------------------------------------------------------------------- view

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Tags className="w-5 h-5 text-gray-700" />
            Tag Management
          </CardTitle>
          <CardDescription>
            View and edit Unity Catalog tags for governed datasets. Changes are proposed as a
            GitHub pull request — git is the source of truth. Once a governance admin merges the PR,
            a GitHub Action applies the tag changes across environments. The app never alters tags
            directly.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Dataset picker */}
          <div className="flex flex-col sm:flex-row sm:items-end gap-3">
            <div className="flex-1">
              <label className="block text-xs font-medium text-gray-500 mb-1">Dataset</label>
              <select
                className="w-full border border-gray-300 rounded-md h-10 px-3 text-sm bg-white"
                value={selectedDataset}
                onChange={(e) => handleSelectDataset(e.target.value)}
                disabled={isLoadingDatasets}
              >
                <option value="">
                  {isLoadingDatasets ? 'Loading datasets...' : 'Select a dataset...'}
                </option>
                {datasets.map((d) => (
                  <option key={d.dataset_id} value={d.dataset_id}>
                    {d.dataset_id}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {!isLoadingDatasets && datasets.length === 0 && (
            <div className="flex items-start gap-2 text-sm text-gray-500 bg-gray-50 border border-gray-200 rounded-md p-3">
              <Info className="w-4 h-4 mt-0.5 flex-shrink-0" />
              <span>
                No governed datasets found. Datasets are tables grouped by the <code>dataset</code> tag
                and appear here once data contracts have been synced (Data Certification tab).
              </span>
            </div>
          )}

          {message && (
            <div
              className={`flex items-start gap-2 text-sm rounded-md p-3 border ${
                message.type === 'success'
                  ? 'bg-green-50 border-green-200 text-green-800'
                  : 'bg-red-50 border-red-200 text-red-800'
              }`}
            >
              {message.type === 'success' ? (
                <CheckCircle2 className="w-4 h-4 mt-0.5 flex-shrink-0" />
              ) : (
                <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
              )}
              <span>{message.text}</span>
            </div>
          )}

          {tablesError && (
            <div className="flex items-start gap-2 text-sm rounded-md p-3 border bg-amber-50 border-amber-200 text-amber-800">
              <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
              <span>{tablesError}</span>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Batch apply */}
      {selectedDataset && tables.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Apply to whole dataset</CardTitle>
            <CardDescription>
              Set tag key/value pairs to apply to every table in this dataset at once. You can still
              fine-tune individual tables below.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {batchRows.map((row, idx) => (
              <div key={idx} className="flex items-center gap-2">
                <input
                  list="suggested-tag-keys"
                  placeholder="key"
                  className="flex-1 border border-gray-300 rounded-md h-9 px-3 text-sm"
                  value={row.key}
                  onChange={(e) =>
                    setBatchRows((prev) => {
                      const r = [...prev];
                      r[idx] = { ...r[idx], key: e.target.value };
                      return r;
                    })
                  }
                />
                <input
                  placeholder="value"
                  className="flex-1 border border-gray-300 rounded-md h-9 px-3 text-sm"
                  value={row.value}
                  onChange={(e) =>
                    setBatchRows((prev) => {
                      const r = [...prev];
                      r[idx] = { ...r[idx], value: e.target.value };
                      return r;
                    })
                  }
                />
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setBatchRows((prev) => prev.filter((_, i) => i !== idx))}
                  title="Remove"
                >
                  <Trash2 className="w-4 h-4 text-gray-500" />
                </Button>
              </div>
            ))}
            <div className="flex items-center gap-2 pt-1">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setBatchRows((prev) => [...prev, { key: '', value: '' }])}
              >
                <Plus className="w-4 h-4 mr-1" /> Add pair
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={applyBatchToAll}
                disabled={Object.keys(buildDesired(batchRows)).length === 0}
              >
                Apply to all {tables.length} tables
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Per-table editing */}
      {selectedDataset && (
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <div>
              <CardTitle className="text-base">Tables</CardTitle>
              <CardDescription>
                {isLoadingTables
                  ? 'Loading current tags...'
                  : `${tables.length} table${tables.length === 1 ? '' : 's'} in this dataset. ${changedTables.length} pending change${changedTables.length === 1 ? '' : 's'}.`}
              </CardDescription>
            </div>
            <Button
              onClick={handleSubmit}
              disabled={isSubmitting || changedTables.length === 0}
              className="text-white"
            >
              {isSubmitting ? (
                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
              ) : (
                <GitPullRequest className="w-4 h-4 mr-2" />
              )}
              Submit as PR
            </Button>
          </CardHeader>
          <CardContent className="space-y-5">
            {isLoadingTables && (
              <div className="flex items-center gap-2 text-sm text-gray-500 py-6 justify-center">
                <Loader2 className="w-4 h-4 animate-spin" /> Loading current tags...
              </div>
            )}

            {!isLoadingTables &&
              tables.map((t) => {
                const diff = diffTable(t.table);
                const rows = edited[t.table] || [];
                return (
                  <div key={t.table} className="border border-gray-200 rounded-lg p-4">
                    <div className="flex items-center justify-between mb-3">
                      <code className="text-sm font-medium text-gray-800">{t.table}</code>
                      {diff.changed && (
                        <span className="text-xs px-2 py-0.5 rounded-full bg-blue-100 text-blue-800">
                          {Object.keys(diff.set).length} set / {diff.unset.length} remove
                        </span>
                      )}
                    </div>
                    <div className="space-y-2">
                      {rows.map((row, idx) => (
                        <div key={idx} className="flex items-center gap-2">
                          <input
                            list="suggested-tag-keys"
                            placeholder="key"
                            className="flex-1 border border-gray-300 rounded-md h-9 px-3 text-sm"
                            value={row.key}
                            onChange={(e) => updateRow(t.table, idx, 'key', e.target.value)}
                          />
                          <span className="text-gray-400">=</span>
                          <input
                            placeholder="value"
                            className="flex-1 border border-gray-300 rounded-md h-9 px-3 text-sm"
                            value={row.value}
                            onChange={(e) => updateRow(t.table, idx, 'value', e.target.value)}
                          />
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => removeRow(t.table, idx)}
                            title="Remove tag"
                          >
                            <Trash2 className="w-4 h-4 text-gray-500" />
                          </Button>
                        </div>
                      ))}
                      {rows.length === 0 && (
                        <p className="text-xs text-gray-400 italic">No editable tags.</p>
                      )}
                      <Button variant="outline" size="sm" onClick={() => addRow(t.table)}>
                        <Plus className="w-4 h-4 mr-1" /> Add tag
                      </Button>
                    </div>
                  </div>
                );
              })}

            {!isLoadingTables && tables.length === 0 && !tablesError && (
              <p className="text-sm text-gray-500 text-center py-6">
                No tables found for this dataset.
              </p>
            )}
          </CardContent>
        </Card>
      )}

      {/* Recent changes */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <div>
            <CardTitle className="text-base">Recent tag changes</CardTitle>
            <CardDescription>Pull requests opened from this tab and their status.</CardDescription>
          </div>
          <Button variant="outline" size="sm" onClick={loadChanges} disabled={isLoadingChanges}>
            <RefreshCw className={`w-4 h-4 ${isLoadingChanges ? 'animate-spin' : ''}`} />
          </Button>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-gray-500 border-b border-gray-200">
                  <th className="py-2 pr-4 font-medium">Dataset</th>
                  <th className="py-2 pr-4 font-medium">Tables</th>
                  <th className="py-2 pr-4 font-medium">Status</th>
                  <th className="py-2 pr-4 font-medium">Submitted</th>
                  <th className="py-2 pr-4 font-medium">PR</th>
                </tr>
              </thead>
              <tbody>
                {isLoadingChanges && (
                  <tr>
                    <td colSpan={5} className="py-6 text-center text-gray-500">
                      <Loader2 className="w-4 h-4 animate-spin inline mr-2" /> Loading...
                    </td>
                  </tr>
                )}
                {!isLoadingChanges && changes.length === 0 && (
                  <tr>
                    <td colSpan={5} className="py-6 text-center text-gray-400">
                      No tag changes submitted yet.
                    </td>
                  </tr>
                )}
                {!isLoadingChanges &&
                  changes.map((c) => {
                    const badge = statusBadge(c.status);
                    return (
                      <tr key={c.id} className="border-b border-gray-100">
                        <td className="py-2 pr-4">{c.dataset_id || c.title}</td>
                        <td className="py-2 pr-4">{c.table_count}</td>
                        <td className="py-2 pr-4">
                          <span className={`text-xs px-2 py-0.5 rounded-full ${badge.className}`}>
                            {badge.label}
                          </span>
                        </td>
                        <td className="py-2 pr-4 text-gray-500">
                          {format(parseUtc(c.created_at), 'MMM d, HH:mm')}
                        </td>
                        <td className="py-2 pr-4">
                          {c.pr_url ? (
                            <a
                              href={c.pr_url}
                              target="_blank"
                              rel="noreferrer"
                              className="inline-flex items-center gap-1 text-primary hover:underline"
                            >
                              {c.pr_number ? `#${c.pr_number}` : 'View'}
                              <ExternalLink className="w-3 h-3" />
                            </a>
                          ) : (
                            <span className="text-gray-400">—</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      {/* Shared datalist of suggested governance keys */}
      <datalist id="suggested-tag-keys">
        {suggestedKeys.map((k) => (
          <option key={k} value={k} />
        ))}
      </datalist>
    </div>
  );
}
