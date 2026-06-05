import { useEffect, useState } from 'react';
import { Bug, Lightbulb, MessageSquare, Loader2, Trash2, RefreshCw } from 'lucide-react';
import { format } from 'date-fns';
import { Card, CardContent } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Textarea } from '../../components/ui/textarea';
import { cn } from '../../lib/utils';
import {
  listFeedback,
  getFeedback,
  updateFeedback,
  deleteFeedback,
  type FeedbackItem,
  type FeedbackType,
  type FeedbackStatus,
} from '../../services/api';

const TYPE_META: Record<FeedbackType, { label: string; icon: React.ReactNode; color: string }> = {
  bug: { label: 'Bug', icon: <Bug className="w-3.5 h-3.5" />, color: 'bg-red-50 text-red-700 border-red-200' },
  feature: { label: 'Feature', icon: <Lightbulb className="w-3.5 h-3.5" />, color: 'bg-amber-50 text-amber-700 border-amber-200' },
  feedback: { label: 'Feedback', icon: <MessageSquare className="w-3.5 h-3.5" />, color: 'bg-blue-50 text-blue-700 border-blue-200' },
};

const STATUS_OPTIONS: { value: FeedbackStatus; label: string }[] = [
  { value: 'open', label: 'Open' },
  { value: 'in_progress', label: 'In progress' },
  { value: 'resolved', label: 'Resolved' },
  { value: 'closed', label: 'Closed' },
  { value: 'wont_fix', label: "Won't fix" },
];

const STATUS_COLOR: Record<FeedbackStatus, string> = {
  open: 'bg-green-50 text-green-700 border-green-200',
  in_progress: 'bg-blue-50 text-blue-700 border-blue-200',
  resolved: 'bg-gray-100 text-gray-600 border-gray-200',
  closed: 'bg-gray-100 text-gray-500 border-gray-200',
  wont_fix: 'bg-gray-100 text-gray-500 border-gray-200',
};

const SEVERITY_COLOR: Record<string, string> = {
  critical: 'text-red-700',
  high: 'text-orange-600',
  medium: 'text-amber-600',
  low: 'text-gray-500',
};

function TypeBadge({ type }: { type: FeedbackType }) {
  const meta = TYPE_META[type];
  return (
    <span className={cn('inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium', meta.color)}>
      {meta.icon}
      {meta.label}
    </span>
  );
}

export function FeedbackAdmin() {
  const [items, setItems] = useState<FeedbackItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [typeFilter, setTypeFilter] = useState<string>('');
  const [statusFilter, setStatusFilter] = useState<string>('open');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<FeedbackItem | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [notes, setNotes] = useState('');
  const [savingNotes, setSavingNotes] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await listFeedback({
        type: typeFilter || undefined,
        status: statusFilter || undefined,
      });
      setItems(list);
      if (selectedId && !list.some((i) => i.id === selectedId)) {
        setSelectedId(null);
        setDetail(null);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load feedback');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [typeFilter, statusFilter]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let mounted = true;
    setLoadingDetail(true);
    (async () => {
      try {
        const d = await getFeedback(selectedId);
        if (mounted) {
          setDetail(d);
          setNotes(d.admin_notes || '');
        }
      } catch (e) {
        if (mounted) setError(e instanceof Error ? e.message : 'Failed to load item');
      } finally {
        if (mounted) setLoadingDetail(false);
      }
    })();
    return () => {
      mounted = false;
    };
  }, [selectedId]);

  const handleStatusChange = async (status: FeedbackStatus) => {
    if (!detail) return;
    try {
      const updated = await updateFeedback(detail.id, { status });
      setDetail(updated);
      setItems((prev) => prev.map((i) => (i.id === updated.id ? { ...i, status: updated.status } : i)));
      // If we're filtering by status, the item may drop out of the list.
      if (statusFilter && status !== statusFilter) {
        await load();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to update status');
    }
  };

  const handleSaveNotes = async () => {
    if (!detail) return;
    setSavingNotes(true);
    try {
      const updated = await updateFeedback(detail.id, { admin_notes: notes });
      setDetail(updated);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save notes');
    } finally {
      setSavingNotes(false);
    }
  };

  const handleDelete = async () => {
    if (!detail) return;
    if (!window.confirm('Delete this feedback item? This cannot be undone.')) return;
    try {
      await deleteFeedback(detail.id);
      setItems((prev) => prev.filter((i) => i.id !== detail.id));
      setSelectedId(null);
      setDetail(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to delete');
    }
  };

  return (
    <div className="space-y-4">
      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <select
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
          className="rounded-md border border-gray-300 px-3 py-1.5 text-sm"
        >
          <option value="">All types</option>
          <option value="bug">Bugs</option>
          <option value="feature">Feature requests</option>
          <option value="feedback">Feedback</option>
        </select>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="rounded-md border border-gray-300 px-3 py-1.5 text-sm"
        >
          <option value="">All statuses</option>
          {STATUS_OPTIONS.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
        <Button variant="outline" size="sm" onClick={load} className="flex items-center gap-2">
          <RefreshCw className={cn('w-4 h-4', loading && 'animate-spin')} />
          Refresh
        </Button>
        <span className="text-sm text-gray-500">{items.length} item(s)</span>
      </div>

      {error && (
        <div className="rounded-md bg-red-50 border border-red-200 px-3 py-2 text-sm text-red-700">{error}</div>
      )}

      <div className="flex gap-6 h-[calc(100vh-260px)]">
        {/* List */}
        <div className="w-96 flex-shrink-0">
          <Card className="h-full flex flex-col">
            <CardContent className="flex-1 overflow-y-auto p-2">
              {loading ? (
                <div className="flex items-center justify-center py-8">
                  <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
                </div>
              ) : items.length === 0 ? (
                <p className="text-sm text-gray-500 text-center py-8">No feedback matches these filters.</p>
              ) : (
                <div className="space-y-1">
                  {items.map((item) => (
                    <button
                      key={item.id}
                      onClick={() => setSelectedId(item.id)}
                      className={cn(
                        'w-full text-left rounded-md px-3 py-2.5 transition-colors',
                        selectedId === item.id ? 'bg-primary/10 ring-1 ring-primary/30' : 'hover:bg-gray-50',
                      )}
                    >
                      <div className="flex items-center justify-between gap-2 mb-1">
                        <TypeBadge type={item.type} />
                        <span
                          className={cn(
                            'rounded-full border px-2 py-0.5 text-[10px] font-medium',
                            STATUS_COLOR[item.status],
                          )}
                        >
                          {STATUS_OPTIONS.find((s) => s.value === item.status)?.label || item.status}
                        </span>
                      </div>
                      <p className="text-sm font-medium text-gray-900 line-clamp-1">{item.title}</p>
                      <p className="text-xs text-gray-400 mt-0.5">
                        {item.submitted_by || 'unknown'}
                        {item.created_at && ` · ${format(new Date(item.created_at), 'MMM d, yyyy')}`}
                        {item.source === 'chat' && ' · via chat'}
                      </p>
                    </button>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        {/* Detail */}
        <div className="flex-1 min-w-0">
          <Card className="h-full flex flex-col">
            <CardContent className="flex-1 overflow-y-auto p-6">
              {!selectedId ? (
                <div className="py-16 text-center text-gray-500">
                  Select a feedback item to view details.
                </div>
              ) : loadingDetail || !detail ? (
                <div className="flex items-center justify-center py-16">
                  <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
                </div>
              ) : (
                <div className="space-y-5">
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <div className="flex items-center gap-2 mb-2">
                        <TypeBadge type={detail.type} />
                        {detail.type === 'bug' && detail.severity && (
                          <span className={cn('text-xs font-semibold capitalize', SEVERITY_COLOR[detail.severity] || 'text-gray-500')}>
                            {detail.severity} severity
                          </span>
                        )}
                      </div>
                      <h2 className="text-xl font-semibold text-gray-900">{detail.title}</h2>
                    </div>
                    <Button variant="ghost" size="sm" onClick={handleDelete} className="text-red-600 hover:bg-red-50 flex items-center gap-1.5">
                      <Trash2 className="w-4 h-4" />
                      Delete
                    </Button>
                  </div>

                  {/* Meta + status control */}
                  <div className="grid grid-cols-2 gap-4 text-sm">
                    <div>
                      <p className="text-xs text-gray-400 uppercase tracking-wide mb-1">Status</p>
                      <select
                        value={detail.status}
                        onChange={(e) => handleStatusChange(e.target.value as FeedbackStatus)}
                        className="rounded-md border border-gray-300 px-2 py-1 text-sm"
                      >
                        {STATUS_OPTIONS.map((s) => (
                          <option key={s.value} value={s.value}>
                            {s.label}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div>
                      <p className="text-xs text-gray-400 uppercase tracking-wide mb-1">Submitted</p>
                      <p className="text-gray-700">
                        {detail.created_at ? format(new Date(detail.created_at), 'MMM d, yyyy HH:mm') : '—'}
                      </p>
                    </div>
                    <div>
                      <p className="text-xs text-gray-400 uppercase tracking-wide mb-1">From</p>
                      <p className="text-gray-700">{detail.submitted_by_name || detail.submitted_by || 'Unknown'}</p>
                    </div>
                    <div>
                      <p className="text-xs text-gray-400 uppercase tracking-wide mb-1">Source</p>
                      <p className="text-gray-700 capitalize">{detail.source}</p>
                    </div>
                  </div>

                  {detail.description && (
                    <div>
                      <p className="text-xs text-gray-400 uppercase tracking-wide mb-1">Details</p>
                      <p className="text-sm text-gray-700 whitespace-pre-wrap">{detail.description}</p>
                    </div>
                  )}

                  {/* Bug diagnostics */}
                  {detail.type === 'bug' && (
                    <div className="space-y-3">
                      {(detail.page_url || detail.app_version || detail.user_agent) && (
                        <div className="rounded-md bg-gray-50 border border-gray-200 p-3 text-xs text-gray-600 space-y-1">
                          {detail.page_url && (
                            <p>
                              <span className="font-medium">Page:</span> {detail.page_url}
                            </p>
                          )}
                          {detail.app_version && (
                            <p>
                              <span className="font-medium">App version:</span> {detail.app_version}
                            </p>
                          )}
                          {detail.user_agent && (
                            <p>
                              <span className="font-medium">Browser:</span> {detail.user_agent}
                            </p>
                          )}
                        </div>
                      )}

                      {detail.console_logs && detail.console_logs.length > 0 && (
                        <details className="rounded-md border border-gray-200">
                          <summary className="cursor-pointer px-3 py-2 text-sm font-medium text-gray-700">
                            Console logs ({detail.console_logs.length})
                          </summary>
                          <div className="max-h-64 overflow-y-auto border-t border-gray-200 bg-gray-900 p-3 font-mono text-xs">
                            {detail.console_logs.map((c, i) => (
                              <div key={i} className="whitespace-pre-wrap break-words mb-1">
                                <span
                                  className={cn(
                                    'mr-2 font-bold',
                                    c.level === 'error' || c.level === 'uncaught' || c.level === 'unhandledrejection'
                                      ? 'text-red-400'
                                      : c.level === 'warn'
                                        ? 'text-amber-400'
                                        : 'text-gray-400',
                                  )}
                                >
                                  [{c.level}]
                                </span>
                                <span className="text-gray-200">{c.message}</span>
                              </div>
                            ))}
                          </div>
                        </details>
                      )}

                      {detail.network_errors && detail.network_errors.length > 0 && (
                        <details className="rounded-md border border-gray-200">
                          <summary className="cursor-pointer px-3 py-2 text-sm font-medium text-gray-700">
                            Failed network requests ({detail.network_errors.length})
                          </summary>
                          <div className="max-h-64 overflow-y-auto border-t border-gray-200 p-3 text-xs space-y-1">
                            {detail.network_errors.map((n, i) => (
                              <div key={i} className="flex items-start gap-2 font-mono break-all">
                                <span className="font-bold text-red-600">{n.status || 'ERR'}</span>
                                <span className="text-gray-500">{n.method}</span>
                                <span className="text-gray-700">{n.url}</span>
                              </div>
                            ))}
                          </div>
                        </details>
                      )}
                    </div>
                  )}

                  {/* Admin notes */}
                  <div>
                    <p className="text-xs text-gray-400 uppercase tracking-wide mb-1">Internal notes</p>
                    <Textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={3} placeholder="Triage notes (visible to admins only)" />
                    <div className="mt-2 flex justify-end">
                      <Button size="sm" onClick={handleSaveNotes} disabled={savingNotes}>
                        {savingNotes ? (
                          <span className="flex items-center gap-2">
                            <Loader2 className="w-4 h-4 animate-spin" /> Saving...
                          </span>
                        ) : (
                          'Save notes'
                        )}
                      </Button>
                    </div>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
