import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import {
  Tags,
  Loader2,
  GitPullRequest,
  ExternalLink,
  RefreshCw,
  AlertCircle,
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
  Filter,
  History,
  Replace,
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
import { BulkTagBar } from '../../components/admin/tags/BulkTagBar';
import { RenameKeyPanel } from '../../components/admin/tags/RenameKeyPanel';
import { TagSearchBox } from '../../components/admin/tags/TagSearchBox';
import { TagTargetRow } from '../../components/admin/tags/TagTargetRow';
import type { ColumnsState } from '../../components/admin/tags/TagTargetRow';
import type { EditTarget, RenameOutcome, TagRow } from '../../components/admin/tags/tagModel';
import {
  SuggestedKeysContext,
  diffTarget,
  errorText,
  isContainer,
  makeTarget,
  matchesFilter,
  renameKeyInRows,
  resetTarget,
  targetKey,
  withTag,
  withoutTag,
} from '../../components/admin/tags/tagModel';

// Objects loaded per request (the API's limit).
const LOAD_CHUNK = 500;
// Tables whose columns are loaded at once during a rename.
const COLUMN_LOADS_AT_ONCE = 8;

function chunks<T>(items: T[], size: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size));
  return out;
}

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

const parseUtc = (value: string): Date =>
  parseISO(/Z|[+-]\d{2}:?\d{2}$/.test(value) ? value : `${value}Z`);

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

function CommentDiff({ before, after }: { before?: string | null; after?: string | null }) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
      <div className="border border-gray-100 rounded-lg p-2.5 bg-gray-50/50">
        <span className="font-semibold text-gray-500 block mb-1.5">Description (Before):</span>
        {before ? (
          <p className="whitespace-pre-wrap text-gray-700">{before}</p>
        ) : (
          <p className="text-gray-400 italic">No description</p>
        )}
      </div>
      <div className="border border-amber-200 rounded-lg p-2.5 bg-amber-50/40">
        <span className="font-semibold text-amber-800 block mb-1.5">Description (After):</span>
        {after ? (
          <p className="whitespace-pre-wrap text-gray-900">{after}</p>
        ) : (
          <p className="text-gray-500 italic">The description will be removed</p>
        )}
      </div>
    </div>
  );
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

export function TagManagement() {
  // ?tab=history opens Change History; the default is Edit Metadata.
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab: 'edit' | 'history' = searchParams.get('tab') === 'history' ? 'history' : 'edit';
  const goToTab = (tab: 'edit' | 'history') =>
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (tab === 'history') next.set('tab', 'history');
        else next.delete('tab');
        return next;
      },
      { replace: true }
    );

  const [modeInfo, setModeInfo] = useState<TagManagerModeResponse | null>(null);
  const [isLoadingMode, setIsLoadingMode] = useState(true);

  const [datasets, setDatasets] = useState<TagDataset[]>([]);
  const [suggestedKeys, setSuggestedKeys] = useState<string[]>([]);

  // The working set: catalogs, schemas, tables and views in display order, plus
  // every loaded target (those and columns) keyed by targetKey().
  const [order, setOrder] = useState<string[]>([]);
  const [targets, setTargets] = useState<Record<string, EditTarget>>({});
  const [columns, setColumns] = useState<Record<string, ColumnsState>>({});
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  // Where the working set came from: each added dataset's table keys, and the
  // tables picked one by one. Unticking a dataset removes only the tables it
  // alone brought in; a lone dataset is recorded on the change.
  const [datasetMembers, setDatasetMembers] = useState<Record<string, string[]>>({});
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [isAdding, setIsAdding] = useState(false);
  const [isRenaming, setIsRenaming] = useState(false);

  const [filter, setFilter] = useState('');
  const [changedOnly, setChangedOnly] = useState(false);

  // Preview & execution modal state
  const [isPreviewOpen, setIsPreviewOpen] = useState(false);
  const [previewTab, setPreviewTab] = useState<'checks' | 'diffs' | 'sql'>('checks');
  const [isPreviewLoading, setIsPreviewLoading] = useState(false);
  const [previewData, setPreviewData] = useState<TagPreviewResponse | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const [isSubmitting, setIsSubmitting] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'info' | 'error'; text: string } | null>(null);

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
      setModeInfo(await api.getTagManagerMode());
    } catch (e) {
      console.error('Failed to load tag manager mode', e);
    } finally {
      setIsLoadingMode(false);
    }
  };

  const loadDatasets = async () => {
    try {
      setDatasets(await api.getTagDatasets());
    } catch (e) {
      console.error('Failed to load tag datasets', e);
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
  }, []);

  // History loads on first view and refreshes each time its tab is opened.
  useEffect(() => {
    if (activeTab === 'history' || changes.length === 0) loadChanges();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab]);

  // ---------------------------------------------------------- working set

  const toTarget = (t: TableTags) =>
    makeTarget(t.table, t.tags, { objectType: t.object_type, exists: t.exists ?? true, comment: t.comment });

  /** Add objects to the working set; with `replace`, refresh ones already in it. */
  const mergeTables = (tables: TableTags[], replace = false) => {
    setTargets((prev) => {
      const next = { ...prev };
      for (const t of tables) {
        const key = targetKey(t.table);
        if (next[key] && !replace) continue;
        next[key] = toTarget(t);
      }
      return next;
    });
    setOrder((prev) => {
      const seen = new Set(prev);
      const added = tables.map((t) => targetKey(t.table)).filter((k) => !seen.has(k) && seen.add(k));
      return [...prev, ...added];
    });
  };

  const handleAddDataset = async (datasetId: string) => {
    setIsAdding(true);
    setMessage(null);
    try {
      const resp = await api.getDatasetTags(datasetId);
      if (resp.suggested_keys?.length) setSuggestedKeys(resp.suggested_keys);
      if (resp.error) setMessage({ type: 'error', text: resp.error });
      else if (resp.tables.length === 0)
        setMessage({ type: 'error', text: `No tables found for dataset '${datasetId}'.` });
      mergeTables(resp.tables);
      if (resp.tables.length > 0)
        setDatasetMembers((prev) => ({ ...prev, [datasetId]: resp.tables.map((t) => targetKey(t.table)) }));
    } catch (e) {
      setMessage({ type: 'error', text: errorText(e, 'Failed to load dataset tables') });
    } finally {
      setIsAdding(false);
    }
  };

  const handleAddObjects = async (fqns: string[]) => {
    setPicked((prev) => new Set([...prev, ...fqns.map((f) => targetKey(f))]));
    const fresh = fqns.filter((f) => !targets[targetKey(f)]);
    if (fresh.length === 0) return;
    setIsAdding(true);
    setMessage(null);
    try {
      const resp = await api.getObjectTags(fresh);
      if (resp.suggested_keys?.length) setSuggestedKeys(resp.suggested_keys);
      mergeTables(resp.tables);
    } catch (e) {
      setMessage({ type: 'error', text: errorText(e, 'Failed to load tags') });
    } finally {
      setIsAdding(false);
    }
  };

  /** Confirm before dropping tables that have unsaved edits. */
  const confirmDiscard = (tableKeys: string[]) => {
    const edited = tableKeys.filter((k) => tableHasChanges(k));
    if (edited.length === 0) return true;
    return window.confirm(
      `${edited.length} of these objects have edits that haven't been applied. Remove them and discard those edits?`
    );
  };

  const handleToggleDataset = async (datasetId: string, add: boolean) => {
    if (add) return handleAddDataset(datasetId);
    const otherDatasets = Object.entries(datasetMembers).filter(([id]) => id !== datasetId);
    const keptElsewhere = new Set([...picked, ...otherDatasets.flatMap(([, keys]) => keys)]);
    const leaving = (datasetMembers[datasetId] ?? []).filter((k) => !keptElsewhere.has(k));
    if (!confirmDiscard(leaving)) return;
    leaving.forEach(removeTable);
    setDatasetMembers(Object.fromEntries(otherDatasets));
  };

  const handleRemoveObject = (fqn: string) => {
    const key = targetKey(fqn);
    if (confirmDiscard([key])) removeTable(key);
  };

  /** Load a table's columns into the working set; returns them as loaded from Unity Catalog. */
  const loadColumns = async (
    parent: { table: string; objectType?: string | null },
    replace = false
  ): Promise<EditTarget[]> => {
    const tableKey = targetKey(parent.table);
    setColumns((prev) => ({
      ...prev,
      [tableKey]: { status: 'loading', keys: prev[tableKey]?.keys ?? [], dataTypes: prev[tableKey]?.dataTypes ?? {} },
    }));
    try {
      const resp = await api.getTableColumnTags(parent.table);
      const loaded = resp.columns.map((c) =>
        makeTarget(parent.table, c.tags, { column: c.column, objectType: parent.objectType })
      );
      setTargets((prev) => {
        const next = { ...prev };
        for (const t of loaded) if (replace || !next[t.key]) next[t.key] = t;
        return next;
      });
      const dataTypes: Record<string, string> = {};
      resp.columns.forEach((c, i) => (dataTypes[loaded[i].key] = c.data_type));
      setColumns((prev) => ({
        ...prev,
        [tableKey]: {
          status: resp.error ? 'error' : 'loaded',
          keys: loaded.map((t) => t.key),
          dataTypes,
          error: resp.error,
        },
      }));
      return loaded;
    } catch (e) {
      setColumns((prev) => ({
        ...prev,
        [tableKey]: { status: 'error', keys: [], dataTypes: {}, error: errorText(e, 'Failed to load columns') },
      }));
      return [];
    }
  };

  const changeRows = (key: string, rows: TagRow[]) =>
    setTargets((prev) => (prev[key] ? { ...prev, [key]: { ...prev[key], rows } } : prev));

  const changeComment = (key: string, comment: string) =>
    setTargets((prev) => (prev[key] ? { ...prev, [key]: { ...prev[key], comment } } : prev));

  const belongsTo = (key: string, tableKey: string) => key === tableKey || key.startsWith(`${tableKey}::`);

  const removeTable = (tableKey: string) => {
    setOrder((prev) => prev.filter((k) => k !== tableKey));
    setTargets((prev) => Object.fromEntries(Object.entries(prev).filter(([k]) => !belongsTo(k, tableKey))));
    setColumns((prev) => Object.fromEntries(Object.entries(prev).filter(([k]) => k !== tableKey)));
    setPicked((prev) => new Set([...prev].filter((k) => k !== tableKey)));
    // A dataset whose last table is removed is no longer "in the list".
    setDatasetMembers((prev) =>
      Object.fromEntries(
        Object.entries(prev)
          .map(([id, keys]) => [id, keys.filter((k) => k !== tableKey)] as const)
          .filter(([, keys]) => keys.length > 0)
      )
    );
  };

  const clearAll = () => {
    if (!confirmDiscard(order)) return;
    setOrder([]);
    setTargets({});
    setColumns({});
    setExpanded(new Set());
    setDatasetMembers({});
    setPicked(new Set());
    setFilter('');
    setChangedOnly(false);
    setPreviewData(null);
  };

  const discardEdits = () =>
    setTargets((prev) => Object.fromEntries(Object.entries(prev).map(([k, t]) => [k, resetTarget(t)])));

  const toggleExpanded = (key: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  /** Can this target's tags be changed in the current mode? */
  const isEditable = (t: EditTarget) => (t.column ? columnsEditable : isContainer(t) ? containersEditable : true);

  /** Set (value) or remove (null) one tag on several targets at once. */
  const applyTagTo = (keys: string[], key: string, value: string | null) =>
    setTargets((prev) => {
      const next = { ...prev };
      for (const k of keys) {
        const t = next[k];
        if (!t || !isEditable(t)) continue;
        next[k] = { ...t, rows: value === null ? withoutTag(t.rows, key) : withTag(t.rows, key, value) };
      }
      return next;
    });

  /**
   * Rename tag key `from` to `to` on several targets, keeping each value.
   * `current` is the working set to count against when it's ahead of state.
   */
  const renameKeyOn = (keys: string[], from: string, to: string, current = targets) => {
    const counts: Record<RenameOutcome | 'skipped', number> = { renamed: 0, conflict: 0, absent: 0, skipped: 0 };
    const editable = keys.filter((k) => {
      const t = current[k];
      if (t && !isEditable(t)) counts.skipped++;
      return t && isEditable(t);
    });
    for (const k of editable) counts[renameKeyInRows(current[k].rows, from, to).outcome]++;
    setTargets((prev) => {
      const next = { ...prev };
      for (const k of editable) if (next[k]) next[k] = { ...next[k], rows: renameKeyInRows(next[k].rows, from, to).rows };
      return next;
    });
    return counts;
  };

  const renameMessage = (
    counts: ReturnType<typeof renameKeyOn>,
    from: string,
    to: string,
    notes: string[] = []
  ): { type: 'info' | 'error'; text: string } => {
    const lines = [
      counts.renamed
        ? `Renamed ${from} to ${to} on ${plural(counts.renamed, 'object')}, keeping each value. Review & Run Checks to apply it.`
        : `Nothing was renamed: no editable object in the list has the tag ${from}.`,
    ];
    if (counts.conflict)
      lines.push(
        `${plural(counts.conflict, 'object')} already had ${to} with a different value and ${
          counts.conflict === 1 ? 'was' : 'were'
        } left unchanged. Filter the list by ${from}=* to sort them out by hand.`
      );
    if (counts.skipped)
      lines.push(
        `${plural(counts.skipped, 'catalog or schema', 'catalogs and schemas')} ${
          counts.skipped === 1 ? 'was' : 'were'
        } skipped because their tags can only be changed in Local Execution Mode.`
      );
    if (counts.renamed > LOAD_CHUNK)
      lines.push(
        `One change can include at most ${LOAD_CHUNK} objects, so remove some from the list and apply the rename in batches.`
      );
    return { type: counts.renamed ? 'info' : 'error', text: [...lines, ...notes].join('\n') };
  };

  /** Rename on everything the list filter shows, and those tables' loaded columns. */
  const handleListRename = (from: string, to: string) => {
    const keys = visibleOrder.flatMap((k) => [k, ...(columnsEditable ? columns[k]?.keys ?? [] : [])]);
    setMessage(renameMessage(renameKeyOn(keys, from, to), from, to));
  };

  /** Find every object (and, where editable, column) tagged `from`, add them, and rename it. */
  const handleGlobalRename = async (from: string, to: string) => {
    setIsRenaming(true);
    setMessage(null);
    try {
      const usage = await api.getTagKeyUsage(from);
      if (!usage.objects.length && !usage.columns.length) {
        setMessage({ type: 'error', text: `Nothing to rename: no catalog, schema, table, view or column has the tag ${from}.` });
        return;
      }
      const columnHits = columnsEditable ? usage.columns : [];
      const parentNames = [...new Set(columnHits.map((c) => c.table))];
      const wanted = [
        ...new Map([...usage.objects.map((o) => o.fqn), ...parentNames].map((n) => [targetKey(n), n])).values(),
      ];

      const loaded: TableTags[] = [];
      for (const chunk of chunks(
        wanted.filter((n) => !targets[targetKey(n)]),
        LOAD_CHUNK
      )) {
        const resp = await api.getObjectTags(chunk);
        if (resp.suggested_keys?.length) setSuggestedKeys(resp.suggested_keys);
        loaded.push(...resp.tables);
      }
      mergeTables(loaded);
      setPicked((prev) => new Set([...prev, ...wanted.map((n) => targetKey(n))]));

      const current: Record<string, EditTarget> = {};
      for (const t of loaded) current[targetKey(t.table)] = toTarget(t);
      const parents = parentNames
        .map((n) => current[targetKey(n)] ?? targets[targetKey(n)])
        .filter((p): p is EditTarget => Boolean(p) && columns[p.key]?.status !== 'loaded');
      for (const batch of chunks(parents, COLUMN_LOADS_AT_ONCE)) {
        for (const cols of await Promise.all(batch.map((p) => loadColumns(p)))) {
          for (const c of cols) current[c.key] = c;
        }
      }
      // What's already in the list, with its edits, wins over a fresh load.
      Object.assign(current, targets);

      const keys = [
        ...usage.objects.map((o) => targetKey(o.fqn)),
        ...columnHits.map((c) => targetKey(c.table, c.column)),
      ];
      const notes: string[] = [];
      if (!columnsEditable && usage.columns.length)
        notes.push(
          `${plural(usage.columns.length, 'column')} tagged ${from} ${
            usage.columns.length === 1 ? 'was' : 'were'
          } left alone because column tags can only be changed in Local Execution Mode.`
        );
      if (usage.truncated)
        notes.push(
          'There were too many uses to load at once, so only some were renamed. Apply this change, then run the rename again to catch the rest.'
        );
      setMessage(renameMessage(renameKeyOn(keys, from, to, current), from, to, notes));
    } catch (e) {
      setMessage({ type: 'error', text: errorText(e, `Failed to rename ${from}`) });
    } finally {
      setIsRenaming(false);
    }
  };

  // ----------------------------------------------------------------- derived

  const columnsEditable = modeInfo?.columns_supported ?? false;
  const containersEditable = modeInfo?.containers_supported ?? false;

  const changedTargets = useMemo(
    () => Object.values(targets).filter((t) => diffTarget(t).changed),
    [targets]
  );

  const tableHasChanges = (tableKey: string) =>
    changedTargets.some((t) => belongsTo(t.key, tableKey));

  const visibleOrder = order.filter((k) => {
    const t = targets[k];
    if (!t) return false;
    if (changedOnly && !tableHasChanges(k)) return false;
    return matchesFilter(t, filter);
  });

  const addedDatasets = Object.keys(datasetMembers);
  const singleDataset = addedDatasets.length === 1 && picked.size === 0 ? addedDatasets[0] : null;

  const scopeLabel = (() => {
    if (singleDataset) return singleDataset;
    const tables = [...new Set(changedTargets.map((t) => t.table))];
    if (tables.length === 1) return tables[0];
    return tables.length ? `${tables[0]} + ${tables.length - 1} more` : 'No changes';
  })();

  // ----------------------------------------------------------------- preview & run

  const buildPayload = () => {
    return {
      dataset_id: singleDataset,
      dataset_name: singleDataset,
      tables: changedTargets.map((t) => {
        const d = diffTarget(t);
        return {
          table: t.table,
          column: t.column ?? null,
          desired_tags: d.desired,
          ...(d.commentChanged ? { desired_comment: t.comment ?? '' } : {}),
        };
      }),
    };
  };

  const handleOpenPreview = async () => {
    if (changedTargets.length === 0) return;
    setIsPreviewOpen(true);
    setPreviewTab('checks');
    setIsPreviewLoading(true);
    setPreviewError(null);
    setPreviewData(null);
    try {
      setPreviewData(await api.previewTagChange(buildPayload()));
    } catch (e: unknown) {
      setPreviewError(errorText(e, 'Failed to run preview and policy checks'));
    } finally {
      setIsPreviewLoading(false);
    }
  };

  const refreshWorkingSet = async () => {
    const tables = order.map((k) => targets[k]?.table).filter((t): t is string => Boolean(t));
    if (tables.length === 0) return;
    for (const chunk of chunks(tables, LOAD_CHUNK)) mergeTables((await api.getObjectTags(chunk)).tables, true);
    await Promise.all(
      Object.keys(columns)
        .filter((k) => targets[k])
        .map((k) => loadColumns(targets[k], true))
    );
  };

  const handleExecuteChange = async () => {
    if (changedTargets.length === 0) return;
    setIsSubmitting(true);
    setMessage(null);

    try {
      const result = await api.createTagChange(buildPayload());
      const count = `${result.table_count} object${result.table_count === 1 ? '' : 's'}`;

      if (result.execution_mode === 'local') {
        if (result.status === 'completed') {
          setMessage({
            type: 'success',
            text: `Changes applied to Unity Catalog for ${count} (${result.applied_count || 0} statement(s) applied, ${result.noop_count || 0} no-op).`,
          });
        } else {
          setMessage({
            type: 'error',
            text: `Some changes failed (${result.failed_count || 0} statement(s)). Open the change in the change history for details.`,
          });
        }
      } else {
        setMessage({
          type: 'success',
          text: `Change submitted for ${count}. A pull request will open shortly for governance review.`,
        });
      }

      setIsPreviewOpen(false);
      await loadChanges();
      await refreshWorkingSet();
    } catch (e: unknown) {
      setMessage({ type: 'error', text: errorText(e, 'Failed to execute the change') });
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
      setHistoryDetail(await api.getTagChangeDetail(changeId));
    } catch (e: unknown) {
      setHistoryDetailError(errorText(e, 'Failed to load change details'));
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
  const hasWorkingSet = order.length > 0;

  return (
    <SuggestedKeysContext.Provider value={suggestedKeys}>
      <div className="space-y-6">
        {/* Page header */}
        <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold text-gray-900 mb-2">Metadata Manager</h1>
            <p className="text-gray-600">
              Find datasets, catalogs, schemas, tables and views, edit their tags (and columns' tags), describe
              catalogs and schemas, rename a tag key everywhere, and review the policy, typo and risk checks before
              anything is applied.
            </p>
          </div>
          {!isLoadingMode && (
            <div
              className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs font-medium shrink-0 ${
                isLocalMode ? 'bg-purple-50 border-purple-200 text-purple-900' : 'bg-blue-50 border-blue-200 text-blue-900'
              }`}
              title={
                isLocalMode
                  ? `Changes are applied directly to Unity Catalog${modeInfo?.ledger_table ? ` and recorded in ${modeInfo.ledger_table}` : ''}.`
                  : `Changes open a pull request against ${modeInfo?.repo || 'the governance repo'} (${modeInfo?.base_branch || 'main'}); merging it applies them.`
              }
            >
              {isLocalMode ? <Zap className="w-3.5 h-3.5 text-purple-600" /> : <GitPullRequest className="w-3.5 h-3.5 text-blue-600" />}
              <span>
                {isLocalMode ? 'Applies directly' : 'Applies via pull request'}
              </span>
              <span className="text-gray-400">|</span>
              <span className="text-gray-600">{modeInfo?.environment || 'dev'}</span>
            </div>
          )}
        </div>

        {/* Tabs */}
        <div className="flex gap-2 border-b border-gray-200">
          <button
            onClick={() => goToTab('edit')}
            className={`px-4 py-2 font-medium text-sm transition-colors ${activeTab === 'edit'
              ? 'border-b-2 border-primary text-primary'
              : 'text-gray-600 hover:text-gray-900'
              }`}
          >
            <Tags className="w-4 h-4 inline mr-2" />
            Edit Metadata
            {changedTargets.length > 0 && (
              <span className="ml-2 rounded-full bg-blue-100 px-1.5 py-0.5 text-[11px] font-semibold text-blue-800">
                {changedTargets.length}
              </span>
            )}
          </button>
          <button
            onClick={() => goToTab('history')}
            className={`px-4 py-2 font-medium text-sm transition-colors ${activeTab === 'history'
              ? 'border-b-2 border-primary text-primary'
              : 'text-gray-600 hover:text-gray-900'
              }`}
          >
            <History className="w-4 h-4 inline mr-2" />
            Change History
          </button>
        </div>

        {message && (
          <div
            className={`flex items-start gap-2.5 text-sm rounded-lg p-3 border ${
              message.type === 'success'
                ? 'bg-emerald-50 border-emerald-200 text-emerald-900'
                : message.type === 'info'
                ? 'bg-blue-50 border-blue-200 text-blue-900'
                : 'bg-rose-50 border-rose-200 text-rose-900'
            }`}
          >
            {message.type === 'success' ? (
              <CheckCircle2 className="w-4 h-4 mt-0.5 shrink-0 text-emerald-600" />
            ) : message.type === 'info' ? (
              <Replace className="w-4 h-4 mt-0.5 shrink-0 text-blue-600" />
            ) : (
              <AlertCircle className="w-4 h-4 mt-0.5 shrink-0 text-rose-600" />
            )}
            <span className="flex-1 leading-relaxed whitespace-pre-wrap">{message.text}</span>
            {message.type === 'success' && activeTab !== 'history' && (
              <button
                type="button"
                onClick={() => goToTab('history')}
                className="text-xs font-semibold underline underline-offset-2 hover:no-underline whitespace-nowrap"
              >
                View in change history
              </button>
            )}
            <button type="button" onClick={() => setMessage(null)} className="p-0.5 rounded hover:bg-black/5" title="Dismiss">
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        )}

        {activeTab === 'edit' && (
          <Card>
            <CardContent className="space-y-3 pt-6">
            <TagSearchBox
              datasets={datasets}
              isDatasetAdded={(id) => id in datasetMembers}
              datasetTableCount={(id) => datasetMembers[id]?.length}
              onToggleDataset={handleToggleDataset}
              isAdded={(fqn) => Boolean(targets[targetKey(fqn)])}
              onAddObjects={handleAddObjects}
              onRemoveObject={handleRemoveObject}
              listSize={order.length}
            />

            {isAdding && (
              <p className="flex items-center gap-2 text-xs text-gray-500">
                <Loader2 className="w-3.5 h-3.5 animate-spin text-blue-600" /> Loading current metadata from Unity Catalog…
              </p>
            )}

            <RenameKeyPanel busy={isRenaming} columnsIncluded={columnsEditable} onRename={handleGlobalRename} />

            </CardContent>
          </Card>
        )}

        {/* Working set */}
        {activeTab === 'edit' && hasWorkingSet && (
          <Card className="border-gray-200 shadow-sm">
            <CardHeader className="pb-3 border-b border-gray-100 space-y-3">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div>
                  <CardTitle className="text-base font-semibold">
                    Editing {order.length} object{order.length === 1 ? '' : 's'}
                  </CardTitle>
                  <CardDescription className="text-xs">
                    {addedDatasets.length > 0 &&
                      `From ${addedDatasets.length > 3 ? `${addedDatasets.length} datasets` : addedDatasets.join(', ')}${picked.size ? ' and individual picks' : ''}. `}
                    Click a row to edit its tags, its description (catalogs and schemas) or its columns.
                  </CardDescription>
                </div>
                <div className="flex items-center gap-2">
                  <div className="relative">
                    <Filter className="w-3.5 h-3.5 text-gray-400 absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none" />
                    <input
                      value={filter}
                      onChange={(e) => setFilter(e.target.value)}
                      placeholder="Filter: name, key=value, !key"
                      aria-label="Filter the list"
                      className="h-8 w-56 pl-8 pr-2.5 rounded-md border border-gray-300 text-xs focus:ring-1 focus:ring-blue-500"
                    />
                  </div>
                  <label className="flex items-center gap-1.5 text-xs text-gray-600 whitespace-nowrap">
                    <input
                      type="checkbox"
                      checked={changedOnly}
                      onChange={(e) => setChangedOnly(e.target.checked)}
                      className="h-3.5 w-3.5 rounded border-gray-300 text-blue-600"
                    />
                    Changed only
                  </label>
                  <Button variant="ghost" size="sm" onClick={clearAll} className="h-8 text-xs text-gray-600">
                    Clear
                  </Button>
                </div>
              </div>

              {/* Apply one tag to everything the filter shows */}
              <div className="rounded-lg bg-gray-50 border border-gray-200 px-3 py-2">
                <BulkTagBar
                  count={visibleOrder.length}
                  noun={visibleOrder.length === 1 ? 'object' : 'objects'}
                  onApply={(k, v) => applyTagTo(visibleOrder, k, v)}
                  onRemove={(k) => applyTagTo(visibleOrder, k, null)}
                  onRename={handleListRename}
                />
              </div>
            </CardHeader>

            <CardContent className="space-y-2 pt-3">
              {visibleOrder.map((k) => (
                <TagTargetRow
                  key={k}
                  target={targets[k]}
                  targets={targets}
                  columns={columns[k]}
                  columnsEditable={columnsEditable}
                  containersEditable={containersEditable}
                  expanded={expanded}
                  onToggle={toggleExpanded}
                  onChangeRows={changeRows}
                  onChangeComment={changeComment}
                  onBulk={applyTagTo}
                  onRemove={() => confirmDiscard([k]) && removeTable(k)}
                  onLoadColumns={() => loadColumns(targets[k])}
                />
              ))}
              {visibleOrder.length === 0 && (
                <p className="text-sm text-gray-500 text-center py-6">Nothing matches the filter.</p>
              )}
            </CardContent>

            {/* Sticky action bar */}
            <div className="sticky bottom-0 z-10 flex items-center justify-between gap-3 border-t border-gray-200 bg-white/95 backdrop-blur px-6 py-3 rounded-b-xl">
              <span className="text-xs text-gray-600">
                {changedTargets.length === 0
                  ? 'No changes yet.'
                  : `${changedTargets.length} object${changedTargets.length === 1 ? '' : 's'} changed`}
              </span>
              <div className="flex items-center gap-2">
                {changedTargets.length > 0 && (
                  <Button variant="ghost" size="sm" onClick={discardEdits} className="text-xs text-gray-600">
                    Discard changes
                  </Button>
                )}
                <Button
                  onClick={handleOpenPreview}
                  disabled={isSubmitting || changedTargets.length === 0}
                  className={`text-white text-xs font-medium shadow-sm ${
                    isLocalMode ? 'bg-purple-600 hover:bg-purple-700' : 'bg-blue-600 hover:bg-blue-700'
                  }`}
                >
                  <ShieldCheck className="w-4 h-4 mr-1.5" />
                  Review & Run Checks ({changedTargets.length})
                </Button>
              </div>
            </div>
          </Card>
        )}

        {/* Change history */}
        {activeTab === 'history' && (
          <Card className="border-gray-200 shadow-sm">
            <CardHeader className="flex flex-row items-center justify-between pb-3 border-b border-gray-100">
              <div>
                <CardTitle className="text-base font-semibold">Change History</CardTitle>
                <CardDescription className="text-xs">
                  Every tag and description change submitted here, with who made it, its checks and risk, and what was
                  applied.
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
                      <th className="py-2.5 px-3 font-semibold">Change</th>
                      <th className="py-2.5 px-3 font-semibold">Execution Mode</th>
                      <th className="py-2.5 px-3 font-semibold">Objects / Statements</th>
                      <th className="py-2.5 px-3 font-semibold">Status</th>
                      <th className="py-2.5 px-3 font-semibold">Submitted by</th>
                      <th className="py-2.5 px-3 font-semibold">Submitted</th>
                      <th className="py-2.5 px-3 font-semibold">Actions / Link</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {isLoadingChanges && (
                      <tr>
                        <td colSpan={7} className="py-8 text-center text-gray-500">
                          <Loader2 className="w-4 h-4 animate-spin inline mr-2 text-blue-600" /> Loading change history...
                        </td>
                      </tr>
                    )}
                    {!isLoadingChanges && changes.length === 0 && (
                      <tr>
                        <td colSpan={7} className="py-8 text-center text-gray-400">
                          No changes recorded yet.
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
                              {c.dataset_id || c.title.replace(/^(Tag|Metadata) change: /, '').replace(/ \(Local\)$/, '')}
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
                                  {c.table_count} object(s) •{' '}
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
                                <span>{c.table_count} object(s)</span>
                              )}
                            </td>
                            <td className="py-3 px-3">
                              <span
                                className={`inline-flex items-center text-[11px] px-2.5 py-0.5 rounded-full font-medium border ${badge.className}`}
                              >
                                {badge.label}
                              </span>
                            </td>
                            <td className="py-3 px-3 text-gray-700">{c.requested_by || '—'}</td>
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
        )}

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
                      Metadata Change Review & Validation
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
                      <span className="font-mono font-medium text-gray-700">{scopeLabel}</span> • {changedTargets.length} object{changedTargets.length === 1 ? '' : 's'} changed
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
                    Plan & Diffs ({previewData?.plan?.diffs?.length ?? changedTargets.length})
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

                        {(previewData.plan?.diffs?.length ?? 0) === 0 && (
                          <p className="p-6 text-center text-xs text-gray-500 bg-white border border-gray-200 rounded-xl">
                            Nothing would change in Unity Catalog.
                            {previewData.policy_violations?.length
                              ? ' See the policy violations on the first tab.'
                              : ' The tags already match.'}
                          </p>
                        )}
                        {previewData.plan?.diffs?.map((d) => (
                          <div key={d.label ?? d.table} className="border border-gray-200 rounded-xl p-4 bg-white shadow-xs space-y-3">
                            <div className="flex items-center justify-between border-b border-gray-100 pb-2.5">
                              <div className="flex items-center gap-2">
                                <Database className="w-4 h-4 text-blue-600" />
                                <code className="text-xs font-bold text-gray-900">{d.label ?? d.table}</code>
                                <span className="text-[10px] uppercase font-semibold text-gray-500 bg-gray-100 px-1.5 py-0.5 rounded">
                                  {d.column ? 'Column' : d.object_type}
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
                                {d.comment_changed && (
                                  <span className="text-amber-700 font-medium ml-2">description</span>
                                )}
                              </div>
                            </div>

                            {d.comment_changed && <CommentDiff before={d.comment_before} after={d.comment_after} />}

                            {(!d.comment_changed ||
                              d.changed_keys?.length > 0 ||
                              d.removed_keys?.length > 0 ||
                              Object.keys(d.before || {}).length > 0) && (
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
                            )}
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
                          {(previewData.plan?.statements?.length ?? 0) === 0 && (
                            <div className="text-gray-400">-- No statements to run.</div>
                          )}
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
                      {historyDetail?.title || 'Change Details'}
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
                              <span className="text-gray-500 block">Objects Changed:</span>
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
                                    <code className="font-semibold text-gray-900">{o.column ? `${o.table}.${o.column}` : o.table}</code>
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
                          <div key={d.label ?? d.table} className="border border-gray-200 rounded-xl p-4 bg-white shadow-xs space-y-3">
                            <div className="flex items-center justify-between border-b border-gray-100 pb-2">
                              <code className="text-xs font-bold text-gray-900">{d.label ?? d.table}</code>
                              <span className="text-[10px] uppercase font-semibold text-gray-500 bg-gray-100 px-1.5 py-0.5 rounded">
                                {d.column ? 'Column' : d.object_type}
                              </span>
                            </div>

                            {d.comment_changed && <CommentDiff before={d.comment_before} after={d.comment_after} />}

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

      </div>
    </SuggestedKeysContext.Provider>
  );
}
