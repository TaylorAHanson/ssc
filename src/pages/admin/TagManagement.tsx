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
  Shield,
  ShieldAlert,
  ShieldCheck,
  AlertTriangle,
  Sparkles,
  Code,
  Check,
  X,
  Layers,
  Terminal,
  Database,
  Copy,
  Zap,
  Eye,
} from 'lucide-react';
import { api } from '../../services/api';
import type {
  TagDataset,
  TableTags,
  TagChange,
  TagManagerModeResponse,
  TagPreviewResponse,
  TagChangeDetail,
} from '../../services/api';
import { format, parseISO } from 'date-fns';

const parseUtc = (value: string): Date =>
  parseISO(/Z|[+-]\d{2}:?\d{2}$/.test(value) ? value : `${value}Z`);

interface TagRow {
  key: string;
  value: string;
}

function statusBadge(status: string, mode?: string): { label: string; className: string } {
  switch (status) {
    case 'completed':
      return {
        label: mode === 'local' ? 'Applied (Direct)' : 'Merged / Applied',
        className: 'bg-emerald-100 text-emerald-800 border-emerald-200',
      };
    case 'provisioning':
      return { label: 'PR Open', className: 'bg-blue-100 text-blue-800 border-blue-200' };
    case 'rejected':
      return { label: 'Closed / Rejected', className: 'bg-gray-100 text-gray-700 border-gray-200' };
    case 'failed':
      return { label: 'Failed', className: 'bg-rose-100 text-rose-800 border-rose-200' };
    default:
      return { label: 'Queued', className: 'bg-amber-100 text-amber-800 border-amber-200' };
  }
}

function riskBandBadge(band: string, score: number) {
  switch (band?.toLowerCase()) {
    case 'low':
      return {
        label: `Low Risk (${score})`,
        bg: 'bg-emerald-50 text-emerald-700 border-emerald-200',
        pill: 'bg-emerald-600',
      };
    case 'medium':
      return {
        label: `Medium Risk (${score})`,
        bg: 'bg-amber-50 text-amber-700 border-amber-200',
        pill: 'bg-amber-500',
      };
    case 'high':
      return {
        label: `High Risk (${score})`,
        bg: 'bg-orange-50 text-orange-700 border-orange-200',
        pill: 'bg-orange-600',
      };
    case 'critical':
      return {
        label: `Critical Risk (${score})`,
        bg: 'bg-rose-50 text-rose-700 border-rose-200',
        pill: 'bg-rose-600',
      };
    default:
      return {
        label: `Risk (${score})`,
        bg: 'bg-gray-50 text-gray-700 border-gray-200',
        pill: 'bg-gray-500',
      };
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
  const [modeInfo, setModeInfo] = useState<TagManagerModeResponse | null>(null);
  const [isLoadingMode, setIsLoadingMode] = useState(true);

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

  // Preview & execution modal state
  const [isPreviewOpen, setIsPreviewOpen] = useState(false);
  const [previewTab, setPreviewTab] = useState<'checks' | 'diffs' | 'sql'>('checks');
  const [isPreviewLoading, setIsPreviewLoading] = useState(false);
  const [previewData, setPreviewData] = useState<TagPreviewResponse | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const [isSubmitting, setIsSubmitting] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  // History detail modal state
  const [selectedHistoryId, setSelectedHistoryId] = useState<string | null>(null);
  const [historyDetail, setHistoryDetail] = useState<TagChangeDetail | null>(null);
  const [isLoadingHistoryDetail, setIsLoadingHistoryDetail] = useState(false);
  const [historyDetailError, setHistoryDetailError] = useState<string | null>(null);
  const [historyTab, setHistoryTab] = useState<'summary' | 'diffs' | 'outcomes' | 'sql'>('summary');

  const [changes, setChanges] = useState<TagChange[]>([]);
  const [isLoadingChanges, setIsLoadingChanges] = useState(true);

  const [copiedSql, setCopiedSql] = useState(false);

  // ---------------------------------------------------------------- load data

  const loadMode = async () => {
    setIsLoadingMode(true);
    try {
      const mode = await api.getTagManagerMode();
      setModeInfo(mode);
    } catch (e) {
      console.error('Failed to load tag manager mode', e);
    } finally {
      setIsLoadingMode(false);
    }
  };

  const loadDatasets = async () => {
    setIsLoadingDatasets(true);
    try {
      const ds = await api.getTagDatasets();
      setDatasets(ds);
    } catch (e) {
      console.error('Failed to load tag datasets', e);
    } finally {
      setIsLoadingDatasets(false);
    }
  };

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
    loadMode();
    loadDatasets();
    loadChanges();
  }, []);

  const loadDatasetTables = async (datasetId: string) => {
    setIsLoadingTables(true);
    setTablesError(null);
    setTables([]);
    setBatchRows([]);
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
    } catch (e: unknown) {
      setTablesError(e instanceof Error ? e.message : 'Failed to load tables');
    } finally {
      setIsLoadingTables(false);
    }
  };

  const handleSelectDataset = (datasetId: string) => {
    setSelectedDataset(datasetId);
    setBatchRows([]);
    setMessage(null);
    setPreviewData(null);
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

  // ----------------------------------------------------------------- preview & run

  const handleOpenPreview = async () => {
    if (changedTables.length === 0) return;
    setIsPreviewOpen(true);
    setPreviewTab('checks');
    setIsPreviewLoading(true);
    setPreviewError(null);

    try {
      const payloadTables = changedTables.map((t) => ({
        table: t.table,
        desired_tags: diffTable(t.table).desired,
      }));
      const preview = await api.previewTagChange({
        dataset_id: selectedDataset,
        dataset_name: selectedDataset,
        tables: payloadTables,
      });
      setPreviewData(preview);
    } catch (e: unknown) {
      setPreviewError(e instanceof Error ? e.message : 'Failed to run preview and policy checks');
    } finally {
      setIsPreviewLoading(false);
    }
  };

  const handleExecuteChange = async () => {
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

      const isLocal = result.execution_mode === 'local';

      if (isLocal) {
        if (result.status === 'completed') {
          setMessage({
            type: 'success',
            text: `Tag changes applied directly to Unity Catalog (${result.applied_count || result.table_count} statement(s) applied, ${result.noop_count || 0} no-op).`,
          });
        } else {
          setMessage({
            type: 'error',
            text: `Local tag execution failed or encountered errors (${result.failed_count || 0} failed). Check history details.`,
          });
        }
      } else {
        setMessage({
          type: 'success',
          text: `Tag change submitted (${result.table_count} table(s)). A pull request will open shortly for governance review.`,
        });
      }

      setIsPreviewOpen(false);
      setBatchRows([]);
      await loadChanges();
      if (selectedDataset) await loadDatasetTables(selectedDataset);
    } catch (e: unknown) {
      setMessage({ type: 'error', text: e instanceof Error ? e.message : 'Failed to execute tag change' });
    } finally {
      setIsSubmitting(false);
    }
  };

  // ---------------------------------------------------------------- history detail

  const handleOpenHistoryDetail = async (changeId: string) => {
    setSelectedHistoryId(changeId);
    setHistoryTab('summary');
    setIsLoadingHistoryDetail(true);
    setHistoryDetailError(null);
    try {
      const detail = await api.getTagChangeDetail(changeId);
      setHistoryDetail(detail);
    } catch (e: unknown) {
      setHistoryDetailError(e instanceof Error ? e.message : 'Failed to load change details');
    } finally {
      setIsLoadingHistoryDetail(false);
    }
  };

  const handleCopySql = (sql: string) => {
    navigator.clipboard.writeText(sql);
    setCopiedSql(true);
    setTimeout(() => setCopiedSql(false), 2000);
  };

  // ------------------------------------------------------------------- render

  const isLocalMode = modeInfo?.local_mode ?? false;

  return (
    <div className="space-y-6">
      {/* Header & Mode Banner */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
            <div>
              <CardTitle className="flex items-center gap-2 text-xl">
                <Tags className="w-5 h-5 text-blue-600" />
                Tag Management
              </CardTitle>
              <CardDescription className="mt-1">
                Govern and apply Unity Catalog tags across datasets with tag policy validation, typo & hygiene scanning, deterministic risk scoring, and advisory AI review.
              </CardDescription>
            </div>

            {/* Mode Indicator Pill */}
            {!isLoadingMode && (
              <div className="flex items-center gap-2 shrink-0">
                <div
                  className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs font-medium ${
                    isLocalMode
                      ? 'bg-purple-50 border-purple-200 text-purple-900'
                      : 'bg-blue-50 border-blue-200 text-blue-900'
                  }`}
                >
                  <span
                    className={`w-2 h-2 rounded-full ${
                      isLocalMode ? 'bg-purple-600 animate-pulse' : 'bg-blue-600'
                    }`}
                  />
                  <span>
                    Mode: <strong>{isLocalMode ? 'Local Execution' : 'GitOps (PR)'}</strong>
                  </span>
                  <span className="text-gray-400">|</span>
                  <span className="text-gray-600">Env: {modeInfo?.environment || 'dev'}</span>
                </div>
              </div>
            )}
          </div>
        </CardHeader>
        <CardContent className="space-y-4 pt-1">
          {/* Mode Explainer Banner */}
          {isLocalMode ? (
            <div className="flex items-start gap-3 text-xs bg-purple-50/70 border border-purple-200 text-purple-900 rounded-lg p-3.5">
              <Zap className="w-4 h-4 text-purple-600 mt-0.5 shrink-0" />
              <div className="flex-1 space-y-1">
                <p className="font-semibold text-purple-950">
                  Local Execution Mode is Active
                </p>
                <p className="text-purple-800 leading-relaxed">
                  Changes are checked against tag policy, scanned for typos/hygiene issues, assessed for risk, reviewed by AI, and applied <strong>directly to Unity Catalog</strong> via SQL statement execution. Results are recorded in the audit ledger
                  {modeInfo?.ledger_table ? <code> ({modeInfo.ledger_table})</code> : ''}.
                  No GitHub PR or Actions required.
                </p>
              </div>
            </div>
          ) : (
            <div className="flex items-start gap-3 text-xs bg-blue-50/70 border border-blue-200 text-blue-900 rounded-lg p-3.5">
              <GitPullRequest className="w-4 h-4 text-blue-600 mt-0.5 shrink-0" />
              <div className="flex-1 space-y-1">
                <p className="font-semibold text-blue-950">GitOps Mode is Active</p>
                <p className="text-blue-800 leading-relaxed">
                  Changes are submitted as pull requests to repository{' '}
                  <code>{modeInfo?.repo || 'configured repo'}</code> on branch{' '}
                  <code>{modeInfo?.base_branch || 'main'}</code>. GitHub Actions will apply changes to Unity Catalog upon merge.
                </p>
              </div>
            </div>
          )}

          {/* Dataset picker */}
          <div className="flex flex-col sm:flex-row sm:items-end gap-3 pt-2">
            <div className="flex-1">
              <label className="block text-xs font-semibold text-gray-700 mb-1.5">
                Select Governed Dataset
              </label>
              <select
                className="w-full border border-gray-300 rounded-lg h-10 px-3 text-sm bg-white shadow-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                value={selectedDataset}
                onChange={(e) => handleSelectDataset(e.target.value)}
                disabled={isLoadingDatasets}
              >
                <option value="">
                  {isLoadingDatasets ? 'Loading datasets...' : 'Select a dataset...'}
                </option>
                {datasets.map((d) => {
                  const scope =
                    d.catalog && d.schema_name
                      ? ` (${d.catalog}.${d.schema_name})`
                      : d.catalog
                      ? ` (${d.catalog})`
                      : '';
                  return (
                    <option key={d.dataset_id} value={d.dataset_id}>
                      {d.dataset_id}{scope}
                    </option>
                  );
                })}
              </select>
            </div>
          </div>

          {!isLoadingDatasets && datasets.length === 0 && (
            <div className="flex items-start gap-2 text-sm text-gray-500 bg-gray-50 border border-gray-200 rounded-md p-3">
              <Info className="w-4 h-4 mt-0.5 flex-shrink-0 text-blue-500" />
              <span>
                No governed datasets found. Datasets are tables grouped by the <code>dataset</code> tag and appear here once data contracts have been synced (Data Certification tab).
              </span>
            </div>
          )}

          {message && (
            <div
              className={`flex items-start gap-2.5 text-sm rounded-lg p-3.5 border ${
                message.type === 'success'
                  ? 'bg-emerald-50 border-emerald-200 text-emerald-900'
                  : 'bg-rose-50 border-rose-200 text-rose-900'
              }`}
            >
              {message.type === 'success' ? (
                <CheckCircle2 className="w-4 h-4 mt-0.5 shrink-0 text-emerald-600" />
              ) : (
                <AlertCircle className="w-4 h-4 mt-0.5 shrink-0 text-rose-600" />
              )}
              <span className="flex-1 leading-relaxed">{message.text}</span>
            </div>
          )}

          {tablesError && (
            <div className="flex items-start gap-2 text-sm rounded-lg p-3.5 border bg-amber-50 border-amber-200 text-amber-900">
              <AlertCircle className="w-4 h-4 mt-0.5 shrink-0 text-amber-600" />
              <span>{tablesError}</span>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Batch apply Card */}
      {selectedDataset && tables.length > 0 && (
        <Card className="border-gray-200 shadow-sm">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-semibold flex items-center gap-2">
              <Layers className="w-4 h-4 text-blue-600" />
              Apply to Whole Dataset ({tables.length} tables)
            </CardTitle>
            <CardDescription className="text-xs">
              Set tag key/value pairs to apply to every table in this dataset at once. You can still fine-tune individual tables below.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2.5">
            {batchRows.map((row, idx) => (
              <div key={idx} className="flex items-center gap-2">
                <input
                  list="suggested-tag-keys"
                  placeholder="tag key (e.g. tier, domain, pii)"
                  className="flex-1 border border-gray-300 rounded-md h-9 px-3 text-sm focus:ring-1 focus:ring-blue-500"
                  value={row.key}
                  onChange={(e) =>
                    setBatchRows((prev) => {
                      const r = [...prev];
                      r[idx] = { ...r[idx], key: e.target.value };
                      return r;
                    })
                  }
                />
                <span className="text-gray-400 font-mono text-xs">=</span>
                <input
                  placeholder="tag value (e.g. Gold, Operations, false)"
                  className="flex-1 border border-gray-300 rounded-md h-9 px-3 text-sm focus:ring-1 focus:ring-blue-500"
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
                  className="hover:bg-rose-50 hover:text-rose-600"
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
                className="text-xs"
              >
                <Plus className="w-3.5 h-3.5 mr-1" /> Add pair
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={applyBatchToAll}
                disabled={Object.keys(buildDesired(batchRows)).length === 0}
                className="text-xs bg-blue-50 hover:bg-blue-100 text-blue-800 border-blue-200"
              >
                Apply to all {tables.length} tables
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Per-table editing & Review Trigger */}
      {selectedDataset && (
        <Card className="border-gray-200 shadow-sm">
          <CardHeader className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-gray-100 pb-4">
            <div>
              <CardTitle className="text-base font-semibold">Table Tags</CardTitle>
              <CardDescription className="text-xs">
                {isLoadingTables
                  ? 'Loading current tags...'
                  : `${tables.length} table${tables.length === 1 ? '' : 's'} in dataset. ${changedTables.length} table${changedTables.length === 1 ? '' : 's'} modified.`}
              </CardDescription>
            </div>
            <div className="flex items-center gap-2">
              <Button
                onClick={handleOpenPreview}
                disabled={isSubmitting || changedTables.length === 0}
                className={`text-white text-xs font-medium shadow-sm ${
                  isLocalMode
                    ? 'bg-purple-600 hover:bg-purple-700'
                    : 'bg-blue-600 hover:bg-blue-700'
                }`}
              >
                <ShieldCheck className="w-4 h-4 mr-1.5" />
                Review & Run Checks ({changedTables.length})
              </Button>
            </div>
          </CardHeader>
          <CardContent className="space-y-4 pt-4">
            {isLoadingTables && (
              <div className="flex items-center gap-2 text-sm text-gray-500 py-8 justify-center">
                <Loader2 className="w-5 h-5 animate-spin text-blue-600" /> Loading tags from Unity Catalog...
              </div>
            )}

            {!isLoadingTables &&
              tables.map((t) => {
                const diff = diffTable(t.table);
                const rows = edited[t.table] || [];
                return (
                  <div
                    key={t.table}
                    className={`border rounded-xl p-4 transition-colors ${
                      diff.changed
                        ? 'border-blue-300 bg-blue-50/20 shadow-xs'
                        : 'border-gray-200 bg-white'
                    }`}
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
                      <div className="flex items-center gap-2">
                        <Database className="w-4 h-4 text-gray-500" />
                        <code className="text-xs font-semibold text-gray-900 bg-gray-100 px-2 py-1 rounded">
                          {t.table}
                        </code>
                      </div>
                      {diff.changed && (
                        <div className="flex items-center gap-1.5">
                          {Object.keys(diff.set).length > 0 && (
                            <span className="text-[11px] px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-800 border border-emerald-200 font-medium">
                              +{Object.keys(diff.set).length} set
                            </span>
                          )}
                          {diff.unset.length > 0 && (
                            <span className="text-[11px] px-2 py-0.5 rounded-full bg-rose-100 text-rose-800 border border-rose-200 font-medium">
                              -{diff.unset.length} remove
                            </span>
                          )}
                        </div>
                      )}
                    </div>
                    <div className="space-y-2">
                      {rows.map((row, idx) => (
                        <div key={idx} className="flex items-center gap-2">
                          <input
                            list="suggested-tag-keys"
                            placeholder="key"
                            className="flex-1 border border-gray-300 rounded-md h-8 px-3 text-xs focus:ring-1 focus:ring-blue-500"
                            value={row.key}
                            onChange={(e) => updateRow(t.table, idx, 'key', e.target.value)}
                          />
                          <span className="text-gray-400 font-mono text-xs">=</span>
                          <input
                            placeholder="value"
                            className="flex-1 border border-gray-300 rounded-md h-8 px-3 text-xs focus:ring-1 focus:ring-blue-500"
                            value={row.value}
                            onChange={(e) => updateRow(t.table, idx, 'value', e.target.value)}
                          />
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => removeRow(t.table, idx)}
                            title="Remove tag"
                            className="h-8 w-8 p-0 hover:bg-rose-50 hover:text-rose-600"
                          >
                            <Trash2 className="w-3.5 h-3.5 text-gray-400" />
                          </Button>
                        </div>
                      ))}
                      {rows.length === 0 && (
                        <p className="text-xs text-gray-400 italic py-1">No tags configured.</p>
                      )}
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => addRow(t.table)}
                        className="text-xs text-gray-600 hover:text-gray-900 h-7 px-2"
                      >
                        <Plus className="w-3 h-3 mr-1" /> Add tag
                      </Button>
                    </div>
                  </div>
                );
              })}

            {!isLoadingTables && tables.length === 0 && !tablesError && (
              <p className="text-sm text-gray-500 text-center py-8">
                No tables found for this dataset.
              </p>
            )}
          </CardContent>
        </Card>
      )}

      {/* Recent changes Table */}
      <Card className="border-gray-200 shadow-sm">
        <CardHeader className="flex flex-row items-center justify-between pb-3 border-b border-gray-100">
          <div>
            <CardTitle className="text-base font-semibold">Recent Tag Changes & Audit Trail</CardTitle>
            <CardDescription className="text-xs">
              History of all tag change submissions, risk assessments, and execution outcomes.
            </CardDescription>
          </div>
          <Button variant="outline" size="sm" onClick={loadChanges} disabled={isLoadingChanges} className="h-8 text-xs">
            <RefreshCw className={`w-3.5 h-3.5 mr-1.5 ${isLoadingChanges ? 'animate-spin' : ''}`} />
            Refresh
          </Button>
        </CardHeader>
        <CardContent className="pt-3">
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-gray-500 border-b border-gray-200 bg-gray-50/50">
                  <th className="py-2.5 px-3 font-semibold">Dataset / Title</th>
                  <th className="py-2.5 px-3 font-semibold">Execution Mode</th>
                  <th className="py-2.5 px-3 font-semibold">Tables / Statements</th>
                  <th className="py-2.5 px-3 font-semibold">Status</th>
                  <th className="py-2.5 px-3 font-semibold">Submitted</th>
                  <th className="py-2.5 px-3 font-semibold">Actions / Link</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {isLoadingChanges && (
                  <tr>
                    <td colSpan={6} className="py-8 text-center text-gray-500">
                      <Loader2 className="w-4 h-4 animate-spin inline mr-2 text-blue-600" /> Loading change history...
                    </td>
                  </tr>
                )}
                {!isLoadingChanges && changes.length === 0 && (
                  <tr>
                    <td colSpan={6} className="py-8 text-center text-gray-400">
                      No tag changes recorded yet.
                    </td>
                  </tr>
                )}
                {!isLoadingChanges &&
                  changes.map((c) => {
                    const badge = statusBadge(c.status, c.execution_mode);
                    const isLocal = c.execution_mode === 'local';
                    return (
                      <tr key={c.id} className="hover:bg-gray-50/80 transition-colors">
                        <td className="py-3 px-3 font-medium text-gray-900">
                          {c.dataset_id || c.title}
                        </td>
                        <td className="py-3 px-3">
                          <span
                            className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium border ${
                              isLocal
                                ? 'bg-purple-50 text-purple-800 border-purple-200'
                                : 'bg-blue-50 text-blue-800 border-blue-200'
                            }`}
                          >
                            {isLocal ? <Zap className="w-3 h-3 text-purple-600" /> : <GitPullRequest className="w-3 h-3 text-blue-600" />}
                            {isLocal ? 'Local Mode' : 'GitOps PR'}
                          </span>
                        </td>
                        <td className="py-3 px-3 text-gray-700">
                          {isLocal ? (
                            <span>
                              {c.table_count} table(s) •{' '}
                              <span className="text-emerald-700 font-medium">
                                {c.applied_count || 0} applied
                              </span>
                              {c.failed_count ? (
                                <span className="text-rose-700 font-medium ml-1">
                                  ({c.failed_count} failed)
                                </span>
                              ) : null}
                            </span>
                          ) : (
                            <span>{c.table_count} table(s)</span>
                          )}
                        </td>
                        <td className="py-3 px-3">
                          <span
                            className={`inline-flex items-center text-[11px] px-2.5 py-0.5 rounded-full font-medium border ${badge.className}`}
                          >
                            {badge.label}
                          </span>
                        </td>
                        <td className="py-3 px-3 text-gray-500 whitespace-nowrap">
                          {format(parseUtc(c.created_at), 'MMM d, HH:mm')}
                        </td>
                        <td className="py-3 px-3 whitespace-nowrap">
                          <div className="flex items-center gap-2">
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => handleOpenHistoryDetail(c.id)}
                              className="h-7 text-[11px] px-2"
                            >
                              <Eye className="w-3 h-3 mr-1" /> Details
                            </Button>
                            {c.pr_url && (
                              <a
                                href={c.pr_url}
                                target="_blank"
                                rel="noreferrer"
                                className="inline-flex items-center gap-1 text-[11px] text-blue-600 hover:underline"
                              >
                                {c.pr_number ? `#${c.pr_number}` : 'PR'}
                                <ExternalLink className="w-3 h-3" />
                              </a>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      {/* =========================================================================
          REVIEW & PREVIEW MODAL
          ========================================================================= */}
      {isPreviewOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-xs p-4 animate-in fade-in">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-[95vw] xl:max-w-[1100px] h-[90vh] flex flex-col overflow-hidden animate-in zoom-in-95">
            {/* Modal Header */}
            <div className="flex items-center justify-between p-4 border-b border-gray-100 bg-white">
              <div className="flex items-center gap-3">
                <div
                  className={`p-2 rounded-xl ${
                    isLocalMode ? 'bg-purple-100 text-purple-700' : 'bg-blue-100 text-blue-700'
                  }`}
                >
                  <ShieldCheck className="w-6 h-6" />
                </div>
                <div>
                  <h3 className="text-lg font-bold text-gray-900 flex items-center gap-2">
                    Tag Change Review & Validation
                    <span
                      className={`text-xs px-2.5 py-0.5 rounded-full font-medium border ${
                        isLocalMode
                          ? 'bg-purple-50 text-purple-800 border-purple-200'
                          : 'bg-blue-50 text-blue-800 border-blue-200'
                      }`}
                    >
                      {isLocalMode ? 'Direct Local Execution' : 'GitOps PR'}
                    </span>
                  </h3>
                  <p className="text-xs text-gray-500 mt-0.5">
                    Dataset: <span className="font-mono font-medium text-gray-700">{selectedDataset}</span> • {changedTables.length} table(s) modified
                  </p>
                </div>
              </div>

              <div className="flex items-center gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setIsPreviewOpen(false)}
                  className="rounded-full h-8 w-8 p-0 hover:bg-gray-100"
                >
                  <X className="w-5 h-5 text-gray-500" />
                </Button>
              </div>
            </div>

            {/* Modal Navigation Tabs */}
            <div className="flex items-center justify-between px-6 border-b border-gray-200 bg-gray-50/50">
              <div className="flex gap-4">
                <button
                  onClick={() => setPreviewTab('checks')}
                  className={`py-3 px-2 text-xs font-semibold border-b-2 transition-colors flex items-center gap-1.5 ${
                    previewTab === 'checks'
                      ? 'border-blue-600 text-blue-600'
                      : 'border-transparent text-gray-600 hover:text-gray-900'
                  }`}
                >
                  <Shield className="w-3.5 h-3.5" />
                  Policy, Risk & AI Review
                  {previewData?.risk && (
                    <span className={`ml-1 px-1.5 py-0.2 rounded text-[10px] ${riskBandBadge(previewData.risk.band, previewData.risk.score).bg}`}>
                      {previewData.risk.band.toUpperCase()}
                    </span>
                  )}
                </button>
                <button
                  onClick={() => setPreviewTab('diffs')}
                  className={`py-3 px-2 text-xs font-semibold border-b-2 transition-colors flex items-center gap-1.5 ${
                    previewTab === 'diffs'
                      ? 'border-blue-600 text-blue-600'
                      : 'border-transparent text-gray-600 hover:text-gray-900'
                  }`}
                >
                  <Layers className="w-3.5 h-3.5" />
                  Plan & Diffs ({previewData?.plan?.diffs?.length || changedTables.length})
                </button>
                <button
                  onClick={() => setPreviewTab('sql')}
                  className={`py-3 px-2 text-xs font-semibold border-b-2 transition-colors flex items-center gap-1.5 ${
                    previewTab === 'sql'
                      ? 'border-blue-600 text-blue-600'
                      : 'border-transparent text-gray-600 hover:text-gray-900'
                  }`}
                >
                  <Code className="w-3.5 h-3.5" />
                  Generated SQL ({previewData?.plan?.statements?.length || 0})
                </button>
              </div>

              {previewData?.risk && (
                <div className="hidden sm:flex items-center gap-2 py-2">
                  <span className="text-xs text-gray-500">Risk Score:</span>
                  <div className={`px-2.5 py-1 rounded-full text-xs font-semibold border flex items-center gap-1.5 ${riskBandBadge(previewData.risk.band, previewData.risk.score).bg}`}>
                    <span className={`w-2 h-2 rounded-full ${riskBandBadge(previewData.risk.band, previewData.risk.score).pill}`} />
                    {riskBandBadge(previewData.risk.band, previewData.risk.score).label}
                  </div>
                </div>
              )}
            </div>

            {/* Modal Body */}
            <div className="flex-1 overflow-y-auto p-6 bg-gray-50/40">
              {isPreviewLoading && (
                <div className="h-full flex flex-col items-center justify-center space-y-3 py-16">
                  <Loader2 className="w-8 h-8 animate-spin text-blue-600" />
                  <p className="text-sm font-medium text-gray-700">
                    Running policy checks, hygiene linting, risk scoring & AI review...
                  </p>
                  <p className="text-xs text-gray-400">
                    Querying live Unity Catalog metadata and tag vocabulary
                  </p>
                </div>
              )}

              {previewError && (
                <div className="p-4 bg-rose-50 border border-rose-200 rounded-xl text-rose-900 space-y-2">
                  <div className="flex items-center gap-2 font-semibold">
                    <AlertCircle className="w-5 h-5 text-rose-600" />
                    Validation Failed
                  </div>
                  <p className="text-xs leading-relaxed whitespace-pre-wrap">{previewError}</p>
                </div>
              )}

              {!isPreviewLoading && previewData && (
                <div className="space-y-6">
                  {/* TAB 1: CHECKS */}
                  {previewTab === 'checks' && (
                    <div className="space-y-6">
                      {/* 1. Advisory AI Agent Review (Up top) */}
                      <div className="border border-purple-200 rounded-xl p-5 bg-gradient-to-br from-purple-50/40 via-white to-purple-50/20 shadow-xs space-y-3">
                        <div className="flex items-center justify-between">
                          <div>
                            <h4 className="text-sm font-bold text-purple-950 flex items-center gap-2">
                              <Sparkles className="w-4 h-4 text-purple-600" />
                              Advisory AI Agent Review
                            </h4>
                            <p className="text-xs text-gray-500 mt-0.5">
                              Model: {previewData.agent_review?.model || 'Claude / GPT-4'} • Non-blocking governance insights
                            </p>
                          </div>
                          {previewData.agent_review?.available ? (
                            <span className="text-xs px-2.5 py-1 rounded-full font-semibold bg-purple-100 text-purple-800 border border-purple-200">
                              Review Available
                            </span>
                          ) : (
                            <span className="text-xs px-2.5 py-1 rounded-full font-medium bg-gray-100 text-gray-600">
                              Skipped
                            </span>
                          )}
                        </div>

                        {previewData.agent_review?.available ? (
                          <div className="space-y-3 pt-2 text-xs">
                            {previewData.agent_review.summary && (
                              <div className="p-3.5 bg-white border border-purple-100 rounded-lg space-y-1 shadow-2xs">
                                <span className="font-semibold text-gray-900">Summary:</span>
                                <p className="text-gray-700 leading-relaxed">{previewData.agent_review.summary}</p>
                              </div>
                            )}

                            {previewData.agent_review.concerns?.length > 0 && (
                              <div className="space-y-1.5">
                                <span className="font-semibold text-gray-900">Identified Concerns:</span>
                                <div className="space-y-1.5">
                                  {previewData.agent_review.concerns.map((c, idx) => (
                                    <div key={idx} className="p-2.5 bg-white border border-purple-100 rounded-lg flex items-start gap-2">
                                      <span className="text-[10px] uppercase font-bold px-1.5 py-0.5 rounded bg-purple-100 text-purple-800">
                                        {c.severity}
                                      </span>
                                      <div className="flex-1">
                                        <code className="text-gray-900 font-semibold">{c.object}:</code>{' '}
                                        <span className="text-gray-700">{c.message}</span>
                                      </div>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            )}

                            {previewData.agent_review.questions?.length > 0 && (
                              <div className="space-y-1">
                                <span className="font-semibold text-gray-900">Key Questions for Reviewer:</span>
                                <ul className="list-disc list-inside bg-white p-3 rounded-lg border border-purple-100 space-y-1 text-gray-700">
                                  {previewData.agent_review.questions.map((q, idx) => (
                                    <li key={idx}>{q}</li>
                                  ))}
                                </ul>
                              </div>
                            )}
                          </div>
                        ) : (
                          <p className="text-xs text-gray-500 italic">
                            {previewData.agent_review?.reason || 'Agent review not configured or offline.'}
                          </p>
                        )}
                      </div>

                      {/* 2. Policy Gate Card */}
                      <div
                        className={`border rounded-xl p-5 bg-white shadow-xs ${
                          previewData.valid
                            ? 'border-emerald-200'
                            : 'border-rose-300 ring-1 ring-rose-200'
                        }`}
                      >
                        <div className="flex items-center justify-between mb-3">
                          <h4 className="text-sm font-bold text-gray-900 flex items-center gap-2">
                            {previewData.valid ? (
                              <CheckCircle2 className="w-5 h-5 text-emerald-600" />
                            ) : (
                              <ShieldAlert className="w-5 h-5 text-rose-600" />
                            )}
                            Tag Policy Gate
                          </h4>
                          <span
                            className={`text-xs px-2.5 py-1 rounded-full font-bold uppercase tracking-wider ${
                              previewData.valid
                                ? 'bg-emerald-100 text-emerald-800'
                                : 'bg-rose-100 text-rose-800'
                            }`}
                          >
                            {previewData.valid ? 'Passed' : 'Violations Detected'}
                          </span>
                        </div>

                        {previewData.policy_violations?.length > 0 ? (
                          <div className="space-y-2 mt-3">
                            <p className="text-xs font-semibold text-rose-800">
                              This change violates the tag policy and cannot be applied:
                            </p>
                            <ul className="space-y-1 text-xs text-rose-700 list-disc list-inside bg-rose-50/70 p-3 rounded-lg border border-rose-100">
                              {previewData.policy_violations.map((v, i) => (
                                <li key={i}>{v}</li>
                              ))}
                            </ul>
                          </div>
                        ) : (
                          <p className="text-xs text-gray-600">
                            All proposed tag keys, allowed values, tag counts, and policy constraints passed validation.
                          </p>
                        )}

                        {previewData.policy_warnings?.length > 0 && (
                          <div className="space-y-1 mt-3">
                            <p className="text-xs font-semibold text-amber-800">Policy Warnings:</p>
                            <ul className="space-y-1 text-xs text-amber-700 list-disc list-inside bg-amber-50 p-2.5 rounded-lg border border-amber-100">
                              {previewData.policy_warnings.map((w, i) => (
                                <li key={i}>{w}</li>
                              ))}
                            </ul>
                          </div>
                        )}
                      </div>

                      {/* 3. Deterministic Risk Assessment Card */}
                      <div className="border border-gray-200 rounded-xl p-5 bg-white shadow-xs space-y-4">
                        <div className="flex items-center justify-between">
                          <div>
                            <h4 className="text-sm font-bold text-gray-900 flex items-center gap-2">
                              <Shield className="w-4 h-4 text-blue-600" />
                              Deterministic Risk Assessment
                            </h4>
                            <p className="text-xs text-gray-500 mt-0.5">
                              Model evaluated based on access control tags, removals, overwrites, certified assets, and blast radius.
                            </p>
                          </div>
                          <div className={`px-3 py-1.5 rounded-full text-xs font-bold border flex items-center gap-2 ${riskBandBadge(previewData.risk.band, previewData.risk.score).bg}`}>
                            <span className={`w-2.5 h-2.5 rounded-full ${riskBandBadge(previewData.risk.band, previewData.risk.score).pill}`} />
                            {riskBandBadge(previewData.risk.band, previewData.risk.score).label}
                          </div>
                        </div>

                        {/* Factors Breakdown */}
                        <div className="space-y-2 pt-2 border-t border-gray-100">
                          <p className="text-xs font-semibold text-gray-700">Risk Factor Breakdown:</p>
                          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                            {previewData.risk.factors?.map((f, i) => (
                              <div key={i} className="p-3 rounded-lg border border-gray-100 bg-gray-50/60 space-y-1">
                                <div className="flex items-center justify-between text-xs font-semibold text-gray-900">
                                  <span>{f.label}</span>
                                  <span className="text-blue-600">+{f.contribution} pts</span>
                                </div>
                                {f.details?.length > 0 && (
                                  <p className="text-[11px] text-gray-500 leading-snug">
                                    {f.details.join(' • ')}
                                  </p>
                                )}
                              </div>
                            ))}
                          </div>
                        </div>
                      </div>

                      {/* 4. Hygiene & Typo Scanner (Lint) */}
                      <div className="border border-gray-200 rounded-xl p-5 bg-white shadow-xs space-y-3">
                        <div className="flex items-center justify-between">
                          <div>
                            <h4 className="text-sm font-bold text-gray-900 flex items-center gap-2">
                              <Sparkles className="w-4 h-4 text-amber-500" />
                              Hygiene & Typo Scanner
                            </h4>
                            <p className="text-xs text-gray-500 mt-0.5">
                              Scanned against live Unity Catalog tag vocabulary to detect whitespace, case collisions, and near-miss values.
                            </p>
                          </div>
                          <span className="text-xs font-medium text-gray-500 bg-gray-100 px-2 py-1 rounded">
                            {previewData.lint?.findings?.length || 0} findings
                          </span>
                        </div>

                        {previewData.lint?.findings?.length === 0 ? (
                          <div className="p-3 bg-emerald-50/60 border border-emerald-100 rounded-lg text-xs text-emerald-800 flex items-center gap-2">
                            <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0" />
                            No typo, whitespace, or vocabulary hygiene issues detected.
                          </div>
                        ) : (
                          <div className="space-y-2">
                            {previewData.lint.findings.map((f, idx) => (
                              <div
                                key={idx}
                                className="p-3 rounded-lg border border-amber-200 bg-amber-50/40 text-xs space-y-1"
                              >
                                <div className="flex items-center justify-between">
                                  <span className="font-semibold text-amber-900 flex items-center gap-1.5">
                                    <AlertTriangle className="w-3.5 h-3.5 text-amber-600" />
                                    <code>{f.code}</code> on <code>{f.fqn}</code>
                                  </span>
                                  <span className="text-[10px] uppercase font-bold text-amber-700 bg-amber-100 px-1.5 py-0.5 rounded">
                                    {f.severity}
                                  </span>
                                </div>
                                <p className="text-amber-800">{f.message}</p>
                                {f.suggestions?.length > 0 && (
                                  <div className="pt-1 flex items-center gap-2 text-[11px] text-gray-700">
                                    <span className="font-medium text-gray-900">Suggestions:</span>
                                    {f.suggestions.map((s, sIdx) => (
                                      <span key={sIdx} className="bg-white border border-amber-300 px-2 py-0.5 rounded font-mono text-blue-700">
                                        "{s.value}" ({s.uses} uses in catalog)
                                      </span>
                                    ))}
                                  </div>
                                )}
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  )}

                  {/* TAB 2: DIFFS */}
                  {previewTab === 'diffs' && (
                    <div className="space-y-4">
                      {previewData.plan?.missing_objects && previewData.plan.missing_objects.length > 0 && (
                        <div className="p-3.5 bg-amber-50 border border-amber-200 rounded-xl text-xs text-amber-900 space-y-1.5">
                          <div className="flex items-center gap-1.5 font-semibold text-amber-950">
                            <AlertTriangle className="w-4 h-4 text-amber-600 shrink-0" />
                            <span>Object(s) not currently found in Unity Catalog:</span>
                          </div>
                          <ul className="list-disc list-inside text-amber-800 space-y-0.5 pl-1">
                            {previewData.plan.missing_objects.map((obj, i) => (
                              <li key={i}><code>{obj}</code></li>
                            ))}
                          </ul>
                          <p className="text-[11px] text-amber-700">
                            SQL statements for missing objects may fail at execution if the tables have not been created yet.
                          </p>
                        </div>
                      )}

                      {previewData.plan?.diffs?.map((d) => (
                        <div key={d.table} className="border border-gray-200 rounded-xl p-4 bg-white shadow-xs space-y-3">
                          <div className="flex items-center justify-between border-b border-gray-100 pb-2.5">
                            <div className="flex items-center gap-2">
                              <Database className="w-4 h-4 text-blue-600" />
                              <code className="text-xs font-bold text-gray-900">{d.table}</code>
                              <span className="text-[10px] uppercase font-semibold text-gray-500 bg-gray-100 px-1.5 py-0.5 rounded">
                                {d.object_type}
                              </span>
                            </div>
                            <div className="flex items-center gap-1.5 text-xs">
                              {d.changed_keys?.length > 0 && (
                                <span className="text-emerald-700 font-medium">
                                  {d.changed_keys.length} set/updated
                                </span>
                              )}
                              {d.removed_keys?.length > 0 && (
                                <span className="text-rose-700 font-medium ml-2">
                                  {d.removed_keys.length} removed
                                </span>
                              )}
                            </div>
                          </div>

                          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
                            {/* Before */}
                            <div className="border border-gray-100 rounded-lg p-3 bg-gray-50/50">
                              <span className="font-semibold text-gray-500 block mb-2">Current Tags (Before):</span>
                              {Object.keys(d.before || {}).length === 0 ? (
                                <p className="text-gray-400 italic">No tags currently set</p>
                              ) : (
                                <div className="space-y-1">
                                  {Object.entries(d.before).map(([k, v]) => (
                                    <div key={k} className="flex items-center justify-between font-mono bg-white p-1.5 rounded border border-gray-200">
                                      <span className="text-gray-700">{k}</span>
                                      <span className="text-gray-900 font-semibold">{v}</span>
                                    </div>
                                  ))}
                                </div>
                              )}
                            </div>

                            {/* After */}
                            <div className="border border-blue-100 rounded-lg p-3 bg-blue-50/30">
                              <span className="font-semibold text-blue-700 block mb-2">Target Tags (After):</span>
                              {Object.keys(d.after || {}).length === 0 ? (
                                <p className="text-gray-400 italic">All tags will be removed</p>
                              ) : (
                                <div className="space-y-1">
                                  {Object.entries(d.after).map(([k, v]) => {
                                    const isNew = !(k in d.before);
                                    const isModified = k in d.before && d.before[k] !== v;
                                    return (
                                      <div
                                        key={k}
                                        className={`flex items-center justify-between font-mono p-1.5 rounded border ${
                                          isNew
                                            ? 'bg-emerald-50 border-emerald-200 text-emerald-900'
                                            : isModified
                                            ? 'bg-amber-50 border-amber-200 text-amber-900'
                                            : 'bg-white border-gray-200 text-gray-700'
                                        }`}
                                      >
                                        <span>{k}</span>
                                        <span className="font-semibold">{v}</span>
                                      </div>
                                    );
                                  })}
                                </div>
                              )}
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* TAB 3: SQL */}
                  {previewTab === 'sql' && (
                    <div className="space-y-3">
                      <div className="flex items-center justify-between">
                        <p className="text-xs text-gray-600 font-medium">
                          Exact SQL statements that will be executed {isLocalMode ? 'directly against Unity Catalog' : 'via GitOps Action'}:
                        </p>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => handleCopySql(previewData.plan?.statements?.join('\n') || '')}
                          className="h-7 text-xs"
                        >
                          {copiedSql ? <Check className="w-3.5 h-3.5 mr-1 text-emerald-600" /> : <Copy className="w-3.5 h-3.5 mr-1" />}
                          {copiedSql ? 'Copied' : 'Copy SQL'}
                        </Button>
                      </div>
                      <div className="bg-gray-900 text-gray-100 p-4 rounded-xl font-mono text-xs overflow-x-auto space-y-2 border border-gray-800">
                        {previewData.plan?.statements?.map((stmt, idx) => (
                          <div key={idx} className="leading-relaxed hover:bg-gray-800/80 p-1 rounded">
                            <span className="text-gray-500 mr-2">{idx + 1}.</span>
                            <span className="text-blue-300">{stmt}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Modal Footer */}
            <div className="flex items-center justify-between p-4 border-t border-gray-200 bg-white">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setIsPreviewOpen(false)}
                className="text-xs"
              >
                Close
              </Button>

              <div className="flex items-center gap-3">
                <Button
                  variant="default"
                  size="sm"
                  disabled={isSubmitting || isPreviewLoading || !previewData?.valid}
                  onClick={handleExecuteChange}
                  className={`text-white text-xs font-semibold px-4 py-2 shadow-sm ${
                    isLocalMode
                      ? 'bg-purple-600 hover:bg-purple-700 disabled:bg-purple-300'
                      : 'bg-blue-600 hover:bg-blue-700 disabled:bg-blue-300'
                  }`}
                >
                  {isSubmitting ? (
                    <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  ) : isLocalMode ? (
                    <Zap className="w-4 h-4 mr-1.5" />
                  ) : (
                    <GitPullRequest className="w-4 h-4 mr-1.5" />
                  )}
                  {isLocalMode ? 'Apply Directly to Unity Catalog' : 'Submit as Pull Request'}
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* =========================================================================
          HISTORICAL DETAIL INSPECTOR MODAL
          ========================================================================= */}
      {selectedHistoryId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-xs p-4 animate-in fade-in">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-[95vw] xl:max-w-[1100px] h-[90vh] flex flex-col overflow-hidden animate-in zoom-in-95">
            {/* Header */}
            <div className="flex items-center justify-between p-4 border-b border-gray-100 bg-white">
              <div className="flex items-center gap-3">
                <div
                  className={`p-2 rounded-xl ${
                    historyDetail?.execution_mode === 'local'
                      ? 'bg-purple-100 text-purple-700'
                      : 'bg-blue-100 text-blue-700'
                  }`}
                >
                  <Eye className="w-6 h-6" />
                </div>
                <div>
                  <h3 className="text-lg font-bold text-gray-900 flex items-center gap-2">
                    {historyDetail?.title || 'Tag Change Details'}
                    {historyDetail?.status && (
                      <span className={`text-xs px-2.5 py-0.5 rounded-full font-medium border ${statusBadge(historyDetail.status, historyDetail.execution_mode).className}`}>
                        {statusBadge(historyDetail.status, historyDetail.execution_mode).label}
                      </span>
                    )}
                  </h3>
                  <p className="text-xs text-gray-500 mt-0.5 font-mono">
                    ID: {selectedHistoryId} • Mode: {historyDetail?.execution_mode === 'local' ? 'Local Execution' : 'GitOps PR'}
                  </p>
                </div>
              </div>

              <Button
                variant="ghost"
                size="sm"
                onClick={() => setSelectedHistoryId(null)}
                className="rounded-full h-8 w-8 p-0 hover:bg-gray-100"
              >
                <X className="w-5 h-5 text-gray-500" />
              </Button>
            </div>

            {/* Tab Header */}
            <div className="flex items-center justify-between px-6 border-b border-gray-200 bg-gray-50/50">
              <div className="flex gap-4">
                <button
                  onClick={() => setHistoryTab('summary')}
                  className={`py-3 px-2 text-xs font-semibold border-b-2 transition-colors flex items-center gap-1.5 ${
                    historyTab === 'summary'
                      ? 'border-blue-600 text-blue-600'
                      : 'border-transparent text-gray-600 hover:text-gray-900'
                  }`}
                >
                  <Shield className="w-3.5 h-3.5" />
                  Checks & Risk
                </button>
                <button
                  onClick={() => setHistoryTab('outcomes')}
                  className={`py-3 px-2 text-xs font-semibold border-b-2 transition-colors flex items-center gap-1.5 ${
                    historyTab === 'outcomes'
                      ? 'border-blue-600 text-blue-600'
                      : 'border-transparent text-gray-600 hover:text-gray-900'
                  }`}
                >
                  <Terminal className="w-3.5 h-3.5" />
                  Execution Outcomes ({historyDetail?.outcomes?.length || 0})
                </button>
                <button
                  onClick={() => setHistoryTab('diffs')}
                  className={`py-3 px-2 text-xs font-semibold border-b-2 transition-colors flex items-center gap-1.5 ${
                    historyTab === 'diffs'
                      ? 'border-blue-600 text-blue-600'
                      : 'border-transparent text-gray-600 hover:text-gray-900'
                  }`}
                >
                  <Layers className="w-3.5 h-3.5" />
                  Plan Diffs
                </button>
              </div>

              {historyDetail?.risk && (
                <div className="hidden sm:flex items-center gap-2 py-2">
                  <span className="text-xs text-gray-500">Assessed Risk:</span>
                  <div className={`px-2.5 py-1 rounded-full text-xs font-semibold border flex items-center gap-1.5 ${riskBandBadge(historyDetail.risk.band, historyDetail.risk.score).bg}`}>
                    <span className={`w-2 h-2 rounded-full ${riskBandBadge(historyDetail.risk.band, historyDetail.risk.score).pill}`} />
                    {riskBandBadge(historyDetail.risk.band, historyDetail.risk.score).label}
                  </div>
                </div>
              )}
            </div>

            {/* Body */}
            <div className="flex-1 overflow-y-auto p-6 bg-gray-50/40">
              {isLoadingHistoryDetail && (
                <div className="h-full flex flex-col items-center justify-center space-y-3 py-16">
                  <Loader2 className="w-8 h-8 animate-spin text-blue-600" />
                  <p className="text-sm font-medium text-gray-700">Loading change details...</p>
                </div>
              )}

              {historyDetailError && (
                <div className="p-4 bg-rose-50 border border-rose-200 rounded-xl text-rose-900 text-xs">
                  {historyDetailError}
                </div>
              )}

              {!isLoadingHistoryDetail && historyDetail && (
                <div className="space-y-6">
                  {/* SUMMARY TAB */}
                  {historyTab === 'summary' && (
                    <div className="space-y-5">
                      {/* Execution Overview Card */}
                      <div className="border border-gray-200 rounded-xl p-5 bg-white shadow-xs space-y-3">
                        <h4 className="text-sm font-bold text-gray-900">Execution Summary</h4>
                        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-xs">
                          <div className="p-3 bg-gray-50 rounded-lg">
                            <span className="text-gray-500 block">Execution Mode:</span>
                            <span className="font-semibold text-gray-900 uppercase">
                              {historyDetail.execution_mode}
                            </span>
                          </div>
                          <div className="p-3 bg-gray-50 rounded-lg">
                            <span className="text-gray-500 block">Tables Modified:</span>
                            <span className="font-semibold text-gray-900">
                              {historyDetail.table_count}
                            </span>
                          </div>
                          <div className="p-3 bg-emerald-50 rounded-lg text-emerald-900">
                            <span className="text-emerald-700 block">Statements Applied:</span>
                            <span className="font-semibold">
                              {historyDetail.applied_count || 0}
                            </span>
                          </div>
                          <div className="p-3 bg-gray-50 rounded-lg">
                            <span className="text-gray-500 block">No-op / Unchanged:</span>
                            <span className="font-semibold text-gray-900">
                              {historyDetail.noop_count || 0}
                            </span>
                          </div>
                        </div>

                        {historyDetail.error && (
                          <div className="p-3 bg-rose-50 border border-rose-200 rounded-lg text-rose-800 text-xs mt-2">
                            <strong>Error:</strong> {historyDetail.error}
                          </div>
                        )}
                      </div>

                      {/* AI Review */}
                      {historyDetail.agent_review?.summary && (
                        <div className="border border-purple-100 rounded-xl p-5 bg-purple-50/20 shadow-xs space-y-2">
                          <h4 className="text-sm font-bold text-purple-950 flex items-center gap-2">
                            <Sparkles className="w-4 h-4 text-purple-600" />
                            Advisory AI Agent Review
                          </h4>
                          <p className="text-xs text-gray-700 leading-relaxed bg-white p-3 rounded-lg border border-purple-100">
                            {historyDetail.agent_review.summary}
                          </p>
                        </div>
                      )}

                      {/* Risk Factors Card */}
                      {historyDetail.risk && (
                        <div className="border border-gray-200 rounded-xl p-5 bg-white shadow-xs space-y-3">
                          <h4 className="text-sm font-bold text-gray-900">Assessed Risk Factors</h4>
                          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                            {historyDetail.risk.factors?.map((f, i) => (
                              <div key={i} className="p-3 rounded-lg border border-gray-100 bg-gray-50/60 text-xs space-y-1">
                                <div className="flex items-center justify-between font-semibold text-gray-900">
                                  <span>{f.label}</span>
                                  <span className="text-blue-600">+{f.contribution} pts</span>
                                </div>
                                {f.details?.length > 0 && (
                                  <p className="text-[11px] text-gray-500 leading-snug">
                                    {f.details.join(' • ')}
                                  </p>
                                )}
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  {/* OUTCOMES TAB */}
                  {historyTab === 'outcomes' && (
                    <div className="space-y-3">
                      {historyDetail.outcomes && historyDetail.outcomes.length > 0 ? (
                        <div className="space-y-2">
                          {historyDetail.outcomes.map((o, idx) => (
                            <div
                              key={idx}
                              className={`p-3.5 rounded-xl border text-xs space-y-2 ${
                                o.status === 'applied'
                                  ? 'bg-white border-emerald-200'
                                  : o.status === 'noop'
                                  ? 'bg-gray-50 border-gray-200'
                                  : 'bg-rose-50 border-rose-200 text-rose-900'
                              }`}
                            >
                              <div className="flex items-center justify-between">
                                <div className="flex items-center gap-2">
                                  <Database className="w-3.5 h-3.5 text-gray-500" />
                                  <code className="font-semibold text-gray-900">{o.table}</code>
                                  <span className="text-[10px] uppercase font-bold px-1.5 py-0.5 rounded bg-gray-100 text-gray-700">
                                    {o.operation}
                                  </span>
                                </div>
                                <span
                                  className={`text-[11px] uppercase font-bold px-2 py-0.5 rounded-full ${
                                    o.status === 'applied'
                                      ? 'bg-emerald-100 text-emerald-800'
                                      : o.status === 'noop'
                                      ? 'bg-gray-200 text-gray-700'
                                      : 'bg-rose-100 text-rose-800'
                                  }`}
                                >
                                  {o.status}
                                </span>
                              </div>
                              <div className="bg-gray-900 text-gray-200 p-2 rounded font-mono text-[11px] overflow-x-auto">
                                {o.sql}
                              </div>
                              {o.detail && (
                                <p className="text-[11px] text-gray-500">{o.detail}</p>
                              )}
                            </div>
                          ))}
                        </div>
                      ) : (
                        <div className="p-8 text-center text-gray-400 text-xs">
                          No direct statement execution outcomes recorded for this request (e.g. submitted via GitOps).
                        </div>
                      )}
                    </div>
                  )}

                  {/* DIFFS TAB */}
                  {historyTab === 'diffs' && (
                    <div className="space-y-4">
                      {historyDetail.plan?.diffs?.map((d) => (
                        <div key={d.table} className="border border-gray-200 rounded-xl p-4 bg-white shadow-xs space-y-3">
                          <div className="flex items-center justify-between border-b border-gray-100 pb-2">
                            <code className="text-xs font-bold text-gray-900">{d.table}</code>
                            <span className="text-[10px] uppercase font-semibold text-gray-500 bg-gray-100 px-1.5 py-0.5 rounded">
                              {d.object_type}
                            </span>
                          </div>

                          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
                            <div className="border border-gray-100 rounded-lg p-2.5 bg-gray-50/50">
                              <span className="font-semibold text-gray-500 block mb-1.5">Before:</span>
                              <div className="space-y-1">
                                {Object.entries(d.before || {}).map(([k, v]) => (
                                  <div key={k} className="flex items-center justify-between font-mono bg-white p-1 rounded border border-gray-200">
                                    <span>{k}</span>
                                    <span className="font-semibold">{v}</span>
                                  </div>
                                ))}
                              </div>
                            </div>
                            <div className="border border-blue-100 rounded-lg p-2.5 bg-blue-50/30">
                              <span className="font-semibold text-blue-700 block mb-1.5">After:</span>
                              <div className="space-y-1">
                                {Object.entries(d.after || {}).map(([k, v]) => (
                                  <div key={k} className="flex items-center justify-between font-mono bg-white p-1 rounded border border-blue-200">
                                    <span>{k}</span>
                                    <span className="font-semibold">{v}</span>
                                  </div>
                                ))}
                              </div>
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Footer */}
            <div className="flex items-center justify-end p-4 border-t border-gray-200 bg-white">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setSelectedHistoryId(null)}
                className="text-xs"
              >
                Close
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Shared datalist of suggested governance keys */}
      <datalist id="suggested-tag-keys">
        {suggestedKeys.map((k) => (
          <option key={k} value={k} />
        ))}
      </datalist>
    </div>
  );
}
