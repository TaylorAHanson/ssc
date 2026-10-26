import { useEffect, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  GitBranch,
  Loader2,
  Lock,
  RefreshCw,
} from 'lucide-react';
import { Button } from '../ui/button';
import { api } from '../../services/api';
import type { ContextGitStatus, ContextGitSyncReport } from '../../services/api';

interface Props {
  /** Called with every loaded status, so the page can hide edit controls. */
  onStatusChange?: (status: ContextGitStatus) => void;
  /** Called after a sync that may have changed the catalog. */
  onSynced?: () => void;
}

const POLL_MS = 15000;

function when(value: string | null): string {
  if (!value) return '—';
  const d = new Date(value.endsWith('Z') || value.includes('+') ? value : `${value}Z`);
  return d.toLocaleString();
}

function summarize(report: ContextGitSyncReport): string {
  const docs = report.pulled.documents;
  const domains = report.pulled.domains;
  const parts: string[] = [];
  const imported = docs.created.length + docs.updated.length + domains.created.length + domains.updated.length;
  const removed = docs.deleted.length + domains.deleted.length;
  if (imported) parts.push(`${imported} imported`);
  if (removed) parts.push(`${removed} removed`);
  if (report.commit) parts.push(`${report.pushed.length} file(s) committed`);
  if (report.conflicts.length) parts.push(`${report.conflicts.length} conflict(s)`);
  return parts.length ? parts.join(' · ') : 'Already up to date';
}

/** Git sync status, "Sync from Git", and the read-only banner for the Context Catalog page. */
export function ContextCatalogGitPanel({ onStatusChange, onSynced }: Props) {
  const [status, setStatus] = useState<ContextGitStatus | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = async () => {
    try {
      const s = await api.getContextCatalogGitStatus();
      setStatus(s);
      onStatusChange?.(s);
    } catch {
      // The panel is supplementary; the catalog stays usable without it.
    }
  };

  useEffect(() => {
    load();
    const timer = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const syncNow = async () => {
    setSyncing(true);
    setError(null);
    setNotice(null);
    try {
      const report = await api.syncContextCatalogFromGit();
      setNotice(summarize(report));
      onSynced?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Sync failed');
    } finally {
      setSyncing(false);
      load();
    }
  };

  if (!status) return null;
  const git = status.git;
  const readOnly = !status.direct_editing_enabled;
  if (!git.enabled && !readOnly) return null;

  const lastErrors = git.last_report?.pulled.errors ?? [];

  return (
    <div className="space-y-2">
      {readOnly && (
        <div className="text-sm bg-amber-50 border border-amber-200 text-amber-900 rounded-md px-4 py-3 flex items-start gap-2">
          <Lock className="w-4 h-4 mt-0.5 shrink-0" />
          <div>
            <p className="font-medium">Editing is managed in Git for this environment</p>
            <p className="text-xs mt-0.5">
              Documents and domains can't be changed here. Propose changes as a pull request
              {git.branch ? (
                <>
                  {' '}to the <code className="font-mono">{git.branch}</code> branch
                </>
              ) : null}
              ; they appear here after the next sync.
              {git.branch_url && (
                <>
                  {' '}
                  <a
                    href={git.branch_url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-0.5 underline font-medium"
                  >
                    Open the catalog in Git <ExternalLink className="w-3 h-3" />
                  </a>
                </>
              )}
            </p>
          </div>
        </div>
      )}

      {git.enabled && (
        <div className="border border-gray-200 rounded-md bg-white">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-xs">
            <button
              className="flex items-center gap-1 text-gray-700 font-medium"
              onClick={() => setExpanded((v) => !v)}
              aria-expanded={expanded}
            >
              {expanded ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
              <GitBranch className="w-3.5 h-3.5 text-accent" />
              Git sync
            </button>
            {git.active ? (
              <>
                <span className="text-gray-600">
                  {git.repo}
                  {' @ '}
                  {git.branch_url ? (
                    <a href={git.branch_url} target="_blank" rel="noreferrer" className="text-accent hover:underline">
                      {git.branch}
                    </a>
                  ) : (
                    git.branch
                  )}
                </span>
                <span
                  className={`px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide ${
                    git.push_enabled ? 'bg-green-50 text-green-700' : 'bg-gray-100 text-gray-600'
                  }`}
                  title={
                    git.push_enabled
                      ? 'Saves here are committed to the branch.'
                      : 'Read-only: this environment mirrors the branch and never commits to it.'
                  }
                >
                  {git.push_enabled ? 'Push + pull' : 'Pull only'}
                </span>
                {git.last_error ? (
                  <span className="flex items-center gap-1 text-red-700" title={git.last_error}>
                    <AlertTriangle className="w-3.5 h-3.5" /> Last sync failed
                  </span>
                ) : git.last_sync_at ? (
                  <span className="flex items-center gap-1 text-gray-500">
                    <CheckCircle2 className="w-3.5 h-3.5 text-green-600" /> Synced {when(git.last_sync_at)}
                  </span>
                ) : (
                  <span className="text-gray-500">Not synced yet</span>
                )}
                {git.pending_count > 0 && (
                  <span className="text-amber-700" title="Saved here, waiting to be committed">
                    {git.pending_count} change(s) waiting to commit
                  </span>
                )}
                {git.conflicts.length > 0 && (
                  <span className="text-amber-700">{git.conflicts.length} recent conflict(s)</span>
                )}
                <span className="flex-1" />
                <Button size="sm" variant="outline" onClick={syncNow} disabled={syncing || git.syncing}>
                  {syncing || git.syncing ? (
                    <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" />
                  ) : (
                    <RefreshCw className="w-3.5 h-3.5 mr-1" />
                  )}
                  Sync from Git
                </Button>
              </>
            ) : (
              <span className="text-amber-700">{git.problem}</span>
            )}
          </div>

          {(error || notice) && (
            <div
              className={`mx-3 mb-2 text-xs rounded-md px-3 py-2 flex items-start gap-2 border ${
                error ? 'text-red-600 bg-red-50 border-red-200' : 'text-green-700 bg-green-50 border-green-200'
              }`}
            >
              {error ? <AlertTriangle className="w-4 h-4 shrink-0" /> : <CheckCircle2 className="w-4 h-4 shrink-0" />}
              {error || notice}
            </div>
          )}

          {expanded && git.active && (
            <div className="border-t border-gray-100 px-3 py-3 space-y-3 text-xs">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <Fact label="Environment" value={git.environment || '—'} />
                <Fact label="Folder" value={git.base_path || '(repo root)'} />
                <Fact label="Pull schedule (UTC)" value={git.poll_cron || 'Sync from Git only'} />
                <Fact label="Last import from Git" value={when(git.last_pull_at)} />
                <div>
                  <p className="text-gray-400 uppercase tracking-wide text-[10px] font-semibold">Last commit</p>
                  {git.last_commit_sha && git.last_commit_url ? (
                    <a
                      href={git.last_commit_url}
                      target="_blank"
                      rel="noreferrer"
                      className="mt-0.5 inline-flex items-center gap-0.5 font-mono text-accent hover:underline"
                    >
                      {git.last_commit_sha.slice(0, 8)} <ExternalLink className="w-3 h-3" />
                    </a>
                  ) : (
                    <p className="mt-0.5 text-gray-700">—</p>
                  )}
                  {git.last_commit_at && <p className="text-gray-400">{when(git.last_commit_at)}</p>}
                </div>
                <Fact
                  label="Last sync"
                  value={git.last_sync_at ? `${when(git.last_sync_at)} (${git.last_sync_trigger ?? '—'})` : '—'}
                />
                {git.last_report && <Fact label="Last result" value={summarize(git.last_report)} />}
              </div>

              {git.last_error && (
                <div className="text-red-600 bg-red-50 border border-red-200 rounded-md px-3 py-2">
                  <p className="font-medium">Last error ({when(git.last_error_at)})</p>
                  <p className="mt-0.5 break-words">{git.last_error}</p>
                  <p className="mt-1 text-red-500">
                    Saves keep working; queued changes are committed once the sync succeeds.
                  </p>
                </div>
              )}

              {lastErrors.length > 0 && (
                <div>
                  <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-400 mb-1">
                    Files that couldn't be imported
                  </p>
                  <ul className="space-y-0.5">
                    {lastErrors.map((e) => (
                      <li key={e.path}>
                        <code className="font-mono">{e.path}</code>: <span className="text-gray-600">{e.error}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {git.conflicts.length > 0 && (
                <div>
                  <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-400 mb-1">
                    Recent conflicts (changed here and in Git)
                  </p>
                  <ul className="space-y-0.5">
                    {git.conflicts
                      .slice()
                      .reverse()
                      .map((c) => (
                        <li key={`${c.path}-${c.at}`}>
                          <code className="font-mono">{c.path}</code>{' '}
                          <span className="text-gray-500">
                            · {when(c.at)} · {c.resolution}
                          </span>
                        </li>
                      ))}
                  </ul>
                </div>
              )}

              {git.pending.length > 0 && (
                <div>
                  <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-400 mb-1">
                    Waiting to commit
                  </p>
                  <ul className="space-y-0.5">
                    {git.pending.map((c) => (
                      <li key={c.id} className="text-gray-600">
                        {c.action} {c.subject} <span className="text-gray-400">· {c.email ?? 'unknown'} · {when(c.at)}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              <p className="text-gray-500">
                Settings live under Admin → Settings → Context Catalog → Git sync.
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-gray-400 uppercase tracking-wide text-[10px] font-semibold">{label}</p>
      <p className="mt-0.5 text-gray-700 break-words">{value}</p>
    </div>
  );
}

export default ContextCatalogGitPanel;
