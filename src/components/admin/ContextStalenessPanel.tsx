import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, BellRing, CheckCircle2, Loader2, Megaphone, Send, X } from 'lucide-react';
import { Button } from '../ui/button';
import { api } from '../../services/api';
import type { ContextReminderRun, ContextStaleDocument, ContextStalenessStatus } from '../../services/api';
import { errorMessage } from '../../lib/errors';

interface Props {
  onClose: () => void;
  onOpenDocument: (domainId: string, documentId: string) => void;
}

const RUN_STATUS_TEXT: Record<ContextReminderRun['status'], string> = {
  sent: 'Reminders posted',
  partial: 'Some reminders failed to post',
  failed: 'Reminders failed to post',
  nothing_due: 'Nothing to send',
  not_configured: 'Teams webhook not configured',
  busy: 'A reminder run is already in progress',
};

function describeRun(run: ContextReminderRun): string {
  const parts = [RUN_STATUS_TEXT[run.status] ?? run.status];
  if (run.notified_documents) {
    parts.push(`${run.notified_documents} document(s) for ${run.owners} owner(s)`);
  }
  if (run.skipped_recently_notified) {
    parts.push(`${run.skipped_recently_notified} skipped (reminded recently)`);
  }
  // Each owner's list is capped, so a large first round can take more than one request.
  const unlisted = run.due - run.notified_documents;
  if (run.status === 'sent' && unlisted > 0) {
    parts.push(`${unlisted} more not listed (per-owner limit) — send again for the rest`);
  }
  return parts.join(' · ');
}

/** Stale-document overview + Teams reminder controls for catalog admins. */
export function ContextStalenessPanel({ onClose, onOpenDocument }: Props) {
  const [status, setStatus] = useState<ContextStalenessStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<'test' | 'run' | 'request' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      setStatus(await api.getContextStaleness());
      setError(null);
    } catch (e) {
      setError(errorMessage(e, 'Failed to load stale documents'));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const groups = useMemo(() => {
    const byOwner = new Map<string, ContextStaleDocument[]>();
    for (const d of status?.stale_documents ?? []) {
      const key = d.owner ?? '';
      byOwner.set(key, [...(byOwner.get(key) ?? []), d]);
    }
    return [...byOwner.entries()].sort(([a], [b]) => (a === '' ? 1 : b === '' ? -1 : a.localeCompare(b)));
  }, [status]);

  const sendTest = async () => {
    setBusy('test');
    setError(null);
    setNotice(null);
    try {
      const r = await api.sendContextStaleTestMessage();
      if (r.ok) setNotice('Test message posted. Check the Teams channel — it should mention you.');
      else setError(r.error || 'Teams did not accept the test message');
    } catch (e) {
      setError(errorMessage(e, 'Failed to send test message'));
    } finally {
      setBusy(null);
    }
  };

  const runNow = () =>
    send('run', 'Post reminders for the stale documents to Teams now?', api.runContextStaleReminders);

  const requestAll = () =>
    send(
      'request',
      `Ask the owners of ${status?.needs_review_due ?? 0} document(s) in Teams to review them now? This covers ` +
        'every published document that is stale or has never been marked reviewed, except ones reminded in ' +
        `the last ${status?.renotify_days ?? 7} days.`,
      () => api.requestContextDocumentReviews(),
    );

  const send = async (kind: 'run' | 'request', question: string, call: () => Promise<ContextReminderRun>) => {
    if (!window.confirm(question)) return;
    setBusy(kind);
    setError(null);
    setNotice(null);
    try {
      const run = await call();
      if (run.status === 'failed' || run.status === 'partial' || run.status === 'not_configured') {
        setError(`${describeRun(run)}${run.errors.length ? ` — ${run.errors.join('; ')}` : ''}`);
      } else {
        setNotice(describeRun(run));
      }
      await load();
    } catch (e) {
      setError(errorMessage(e, 'Failed to send reminders'));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl max-h-[88vh] flex flex-col">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200">
          <div className="flex items-center gap-2">
            <BellRing className="w-4 h-4 text-accent" />
            <h2 className="text-sm font-semibold">Stale documents &amp; Teams reminders</h2>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600" aria-label="Close">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-5 space-y-4 overflow-y-auto">
          {loading && !status ? (
            <div className="flex justify-center py-8">
              <Loader2 className="w-5 h-5 animate-spin text-gray-400" />
            </div>
          ) : status ? (
            <>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
                <Fact label="Reminders" value={status.enabled ? 'On' : 'Off'} tone={status.enabled ? 'good' : 'muted'} />
                <Fact label="Schedule (UTC)" value={status.cron || 'Run now only'} />
                <Fact label="Stale after" value={`${status.stale_after_days} days`} />
                <Fact label="Re-remind after" value={`${status.renotify_days} days`} />
                <Fact
                  label="Teams webhook"
                  value={status.webhook_configured ? 'Configured' : 'Not configured'}
                  tone={status.webhook_configured ? 'good' : 'bad'}
                  title={status.webhook_source}
                />
                <Fact
                  label="Links in messages"
                  value={status.app_base_url_set ? 'On' : 'Off — set App base URL'}
                  tone={status.app_base_url_set ? 'good' : 'warn'}
                />
                <Fact
                  label="Next scheduled run"
                  value={status.next_run_at ? new Date(status.next_run_at).toLocaleString() : '—'}
                />
                <Fact
                  label="Last run"
                  value={status.last_run ? new Date(status.last_run.ran_at).toLocaleString() : '—'}
                  title={status.last_run ? describeRun(status.last_run) : undefined}
                />
              </div>
              <p className="text-xs text-gray-500">
                Settings live under Admin → Settings → Context Catalog. Run now works even while scheduled
                reminders are off, and skips documents reminded in the last {status.renotify_days} days.
                Request review from owners also covers the {status.needs_review_count} published document(s)
                that are stale or have never been marked reviewed, which is how to start a first round of
                ownership; scheduled reminders only cover stale ones.
              </p>

              {error && (
                <div className="text-xs text-red-600 bg-red-50 border border-red-200 rounded-md px-3 py-2 flex items-start gap-2">
                  <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" /> {error}
                </div>
              )}
              {notice && (
                <div className="text-xs text-green-700 bg-green-50 border border-green-200 rounded-md px-3 py-2 flex items-start gap-2">
                  <CheckCircle2 className="w-4 h-4 mt-0.5 shrink-0" /> {notice}
                </div>
              )}

              <div>
                <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-400 mb-2">
                  Stale published documents ({status.stale_documents.length})
                </p>
                {groups.length === 0 ? (
                  <p className="text-sm text-gray-500">Every published document is up to date.</p>
                ) : (
                  <div className="space-y-3">
                    {groups.map(([owner, docs]) => (
                      <div key={owner || '__none__'} className="border border-gray-200 rounded-md">
                        <div className="px-3 py-1.5 bg-gray-50 text-xs font-medium text-gray-700 flex items-center justify-between">
                          <span>{owner || 'No owner'}</span>
                          <span className="text-gray-400">
                            {docs[0].owner_source === 'domain' ? 'domain owner · ' : ''}
                            {docs.length} doc{docs.length === 1 ? '' : 's'}
                          </span>
                        </div>
                        <ul className="divide-y divide-gray-100">
                          {docs.map((d) => (
                            <li key={d.id} className="px-3 py-1.5 text-xs flex items-center justify-between gap-2">
                              <button
                                className="text-left text-accent hover:underline truncate"
                                onClick={() => onOpenDocument(d.domain_id, d.id)}
                              >
                                {d.title}
                              </button>
                              <span className="text-gray-400 shrink-0">
                                {d.domain_name} · {d.age_days}d
                                {d.recently_notified ? ' · reminded' : ''}
                              </span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </>
          ) : (
            error && <p className="text-sm text-red-600">{error}</p>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-gray-200">
          <Button variant="outline" onClick={onClose} disabled={busy !== null}>
            Close
          </Button>
          <Button variant="outline" onClick={sendTest} disabled={busy !== null || !status?.webhook_configured}>
            {busy === 'test' ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Send className="w-4 h-4 mr-1" />}
            Send test message
          </Button>
          <Button
            variant="outline"
            onClick={requestAll}
            disabled={busy !== null || !status?.webhook_configured || !status?.needs_review_due}
            title="Ask every owner to review their stale and never-reviewed documents now"
          >
            {busy === 'request' ? (
              <Loader2 className="w-4 h-4 mr-1 animate-spin" />
            ) : (
              <Megaphone className="w-4 h-4 mr-1" />
            )}
            Request review from owners{status?.needs_review_due ? ` (${status.needs_review_due})` : ''}
          </Button>
          <Button onClick={runNow} disabled={busy !== null || !status?.webhook_configured}>
            {busy === 'run' ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <BellRing className="w-4 h-4 mr-1" />}
            Run now
          </Button>
        </div>
      </div>
    </div>
  );
}

function Fact({
  label,
  value,
  tone = 'neutral',
  title,
}: {
  label: string;
  value: string;
  tone?: 'neutral' | 'good' | 'warn' | 'bad' | 'muted';
  title?: string;
}) {
  const color =
    tone === 'good'
      ? 'text-green-700'
      : tone === 'warn'
      ? 'text-amber-700'
      : tone === 'bad'
      ? 'text-red-700'
      : tone === 'muted'
      ? 'text-gray-500'
      : 'text-gray-700';
  return (
    <div title={title}>
      <p className="text-gray-400 uppercase tracking-wide text-[10px] font-semibold">{label}</p>
      <p className={`mt-0.5 truncate ${color}`}>{value}</p>
    </div>
  );
}

export default ContextStalenessPanel;
