import { Fragment, useState } from 'react';
import { Link } from 'react-router-dom';
import cronstrue from 'cronstrue';
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, Download, Loader2, Play, XCircle } from 'lucide-react';
import { api } from '../../services/api';
import type { RbacExportFile, RbacExportRun, RbacExportTarget, RbacPathCheckResult, RbacPermissions } from '../../types/rbac';
import { useVisibleInterval } from '../../hooks/useVisibleInterval';
import { Button } from '../ui/button';
import { Card, CardContent } from '../ui/card';
import { CompareCard } from './CompareCard';
import { Badge, CopyButton, EmptyState, ErrorBanner, SectionTitle, Skeleton, Spinner, type Tone, type ToastMessage } from './ui';
import { useLoad } from './useLoad';
import { SETTINGS_PATH, errorMessage, formatBytes, formatTime, plural, relativeTime } from './utils';

const loadExports = () => api.getRbacExports();

const RUN_STATUS: Record<RbacExportRun['status'], { label: string; tone: Tone }> = {
  running: { label: 'Running', tone: 'blue' },
  completed: { label: 'Completed', tone: 'green' },
  partial: { label: 'Partly written', tone: 'amber' },
  skipped: { label: 'Skipped', tone: 'gray' },
  failed: { label: 'Failed', tone: 'red' },
};

const TRIGGERS: Record<RbacExportRun['trigger'], string> = {
  schedule: 'Scheduled',
  manual: 'Export now',
  on_change: 'After a change',
};

const FILE_STATUS: Record<RbacExportFile['status'], { label: string; tone: Tone }> = {
  written: { label: 'Written', tone: 'green' },
  skipped: { label: 'Skipped', tone: 'gray' },
  failed: { label: 'Failed', tone: 'red' },
};

function describeCron(cron: string): string {
  try {
    return `${cronstrue.toString(cron, { throwExceptionOnParseError: true })} (UTC)`;
  } catch {
    return cron;
  }
}

export function ExportsTab({ permissions, notify }: {
  permissions: RbacPermissions;
  notify: (toast: Omit<ToastMessage, 'id'>) => void;
}) {
  const status = useLoad(loadExports);
  const data = status.data;
  const running = !!data?.runs.some((r) => r.status === 'running');
  useVisibleInterval(status.reload, 4000, running);

  if (status.error) {
    return <ErrorBanner message={errorMessage(status.error, 'Could not load the exports')} onRetry={status.reload} />;
  }
  if (!data) return <Skeleton rows={6} />;

  const sheetNames = data.targets.flatMap((t) => t.sheets);

  return (
    <div className="space-y-6">
      <Card>
        <CardContent className="space-y-3 pt-5">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="space-y-1 text-sm text-gray-700">
              <div>
                <span className="font-medium text-gray-900">Schedule:</span>{' '}
                {data.cron ? describeCron(data.cron) : 'none set; files are written only when someone exports.'}
                {data.cron && <code className="ml-2 text-xs text-gray-400">{data.cron}</code>}
              </div>
              <div>
                <span className="font-medium text-gray-900">Next run:</span>{' '}
                {data.next_run_at ? (
                  <span title={relativeTime(data.next_run_at)}>{formatTime(data.next_run_at)} ({relativeTime(data.next_run_at)})</span>
                ) : (
                  '—'
                )}
              </div>
              <div className="text-xs text-gray-500">
                Each run writes the complete current contents of every sheet with a path, one CSV per sheet named after
                it. A scheduled run is skipped when nothing changed since the last export.
              </div>
            </div>
            <div className="flex flex-col items-end gap-2">
              {data.pending_changes ? (
                <Badge tone="amber"><AlertTriangle className="h-3 w-3" /> Changes waiting for the next export</Badge>
              ) : (
                <Badge tone="green"><CheckCircle2 className="h-3 w-3" /> Up to date with the last export</Badge>
              )}
              {status.loading && <Spinner label="Refreshing…" className="text-xs" />}
            </div>
          </div>
          {permissions.can_export && <ExportNow onDone={(run) => {
            status.reload();
            notify({
              text: run.status === 'skipped'
                ? `Export skipped. ${run.warnings[run.warnings.length - 1] ?? 'Nothing changed since the last export.'}`
                : `Export ${RUN_STATUS[run.status]?.label.toLowerCase() ?? run.status}.`,
            });
          }} />}
        </CardContent>
      </Card>

      <section>
        <SectionTitle>Destinations</SectionTitle>
        <Destinations targets={data.targets} canTest={permissions.can_export} />
      </section>

      <section>
        <SectionTitle>Runs</SectionTitle>
        <RunHistory runs={data.runs} />
      </section>

      <CompareCard sheetNames={sheetNames} />
    </div>
  );
}

function ExportNow({ onDone }: { onDone: (run: RbacExportRun) => void }) {
  const [force, setForce] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<RbacExportRun | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.runRbacExport(force);
      setResult(r);
      onDone(r);
    } catch (e) {
      setError(errorMessage(e, 'Export failed'));
    } finally {
      setBusy(false);
    }
  };

  const written = result?.files.filter((f) => f.status === 'written').length ?? 0;
  return (
    <div className="flex flex-wrap items-center gap-3 border-t pt-3">
      <Button onClick={run} disabled={busy} className="gap-2">
        {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />} Export now
      </Button>
      <label className="flex items-center gap-2 text-sm text-gray-600">
        <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />
        Even if nothing changed
      </label>
      {result && (
        <span className="flex flex-wrap items-center gap-2 text-sm text-gray-600">
          <Badge tone={RUN_STATUS[result.status]?.tone ?? 'gray'}>{RUN_STATUS[result.status]?.label ?? result.status}</Badge>
          {result.status !== 'skipped' && `${plural(written, 'file')} written`}
          {result.error && <span className="text-red-700">{result.error}</span>}
        </span>
      )}
      {error && <span className="text-sm text-red-700">{error}</span>}
    </div>
  );
}

function Destinations({ targets, canTest }: { targets: RbacExportTarget[]; canTest: boolean }) {
  const [checks, setChecks] = useState<Record<string, RbacPathCheckResult | { error: string } | 'busy'>>({});

  const test = async (key: string) => {
    setChecks((c) => ({ ...c, [key]: 'busy' }));
    try {
      const result = await api.testRbacExportPath(key);
      setChecks((c) => ({ ...c, [key]: result }));
    } catch (e) {
      setChecks((c) => ({ ...c, [key]: { error: errorMessage(e, 'Test failed') } }));
    }
  };

  if (targets.length === 0) {
    return <EmptyState title="No destinations">Export paths are set per environment in Admin → Settings → Access Rules.</EmptyState>;
  }
  return (
    <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
      <table className="w-full text-sm">
        <thead className="border-b bg-gray-50 text-left text-xs text-gray-500">
          <tr>
            <th scope="col" className="px-3 py-2 font-medium">Target</th>
            <th scope="col" className="px-3 py-2 font-medium">Path</th>
            <th scope="col" className="px-3 py-2 font-medium">Sheets</th>
            {canTest && <th scope="col" className="px-3 py-2 font-medium"><span className="sr-only">Test</span></th>}
          </tr>
        </thead>
        <tbody>
          {targets.map((t) => {
            const check = checks[t.key];
            return (
              <tr key={t.key} className="border-b align-top last:border-b-0">
                <td className="whitespace-nowrap px-3 py-2 font-medium text-gray-900">{t.label}</td>
                <td className="px-3 py-2">
                  {t.configured && t.path ? (
                    <code className="break-all text-xs text-gray-800">{t.path}</code>
                  ) : (
                    <span className="flex items-start gap-1.5 text-xs text-amber-800">
                      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                      <span>
                        No path set — these sheets aren’t exported.{' '}
                        <Link to={SETTINGS_PATH} className="font-medium underline">Set it in Settings</Link>
                      </span>
                    </span>
                  )}
                </td>
                <td className="px-3 py-2">
                  <div className="flex flex-wrap gap-1">
                    {t.sheets.map((s) => <code key={s} className="rounded bg-gray-100 px-1 text-xs text-gray-700">{s}</code>)}
                  </div>
                </td>
                {canTest && (
                  <td className="px-3 py-2">
                    <div className="flex flex-col items-start gap-1">
                      <Button size="sm" variant="outline" disabled={!t.configured || check === 'busy'} onClick={() => test(t.key)}>
                        {check === 'busy' && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
                        Test path
                      </Button>
                      {check && check !== 'busy' && (
                        'error' in check ? (
                          <span className="flex items-start gap-1 text-xs text-red-700"><XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" /> {check.error}</span>
                        ) : (
                          <span className={`flex items-start gap-1 text-xs ${check.ok ? 'text-green-700' : 'text-red-700'}`}>
                            {check.ok ? <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0" /> : <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />}
                            {check.message || (check.ok ? 'The app can write here.' : 'The app can’t write here.')}
                          </span>
                        )
                      )}
                    </div>
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function RunHistory({ runs }: { runs: RbacExportRun[] }) {
  const [expanded, setExpanded] = useState<string | null>(null);
  if (runs.length === 0) return <EmptyState title="No exports yet">Runs appear here with their files and checksums.</EmptyState>;
  return (
    <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
      <table className="w-full text-sm">
        <thead className="border-b bg-gray-50 text-left text-xs text-gray-500">
          <tr>
            <th scope="col" className="w-8 px-2 py-2"><span className="sr-only">Expand</span></th>
            <th scope="col" className="px-3 py-2 font-medium">Status</th>
            <th scope="col" className="px-3 py-2 font-medium">Trigger</th>
            <th scope="col" className="px-3 py-2 font-medium">By</th>
            <th scope="col" className="px-3 py-2 font-medium">Started</th>
            <th scope="col" className="px-3 py-2 font-medium">Finished</th>
            <th scope="col" className="px-3 py-2 font-medium">Files</th>
            <th scope="col" className="px-3 py-2 font-medium">Warnings</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => {
            const open = expanded === run.id;
            const st = RUN_STATUS[run.status] ?? { label: run.status, tone: 'gray' as Tone };
            return (
              <Fragment key={run.id}>
                <tr className="border-b align-top hover:bg-gray-50/50">
                  <td className="px-2 py-2">
                    <button
                      type="button"
                      onClick={() => setExpanded(open ? null : run.id)}
                      aria-expanded={open}
                      aria-label={open ? 'Hide files' : 'Show files'}
                      className="text-gray-400 hover:text-gray-700"
                    >
                      {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                    </button>
                  </td>
                  <td className="px-3 py-2"><Badge tone={st.tone}>{st.label}</Badge></td>
                  <td className="px-3 py-2 text-gray-700">{TRIGGERS[run.trigger] ?? run.trigger}</td>
                  <td className="px-3 py-2 text-gray-700">{run.actor || (run.trigger === 'schedule' ? 'Schedule' : '—')}</td>
                  <td className="whitespace-nowrap px-3 py-2" title={relativeTime(run.started_at)}>{formatTime(run.started_at)}</td>
                  <td className="whitespace-nowrap px-3 py-2">{formatTime(run.finished_at)}</td>
                  <td className="px-3 py-2 text-gray-700">{run.files.length}</td>
                  <td className="px-3 py-2">
                    {run.warnings.length > 0 ? <Badge tone="amber">{run.warnings.length}</Badge> : <span className="text-gray-400">—</span>}
                    {run.error && <div className="mt-1 text-xs text-red-700">{run.error}</div>}
                  </td>
                </tr>
                {open && (
                  <tr className="border-b bg-gray-50/60">
                    <td colSpan={8} className="space-y-3 px-4 py-3">
                      {run.warnings.length > 0 && (
                        <ul className="list-disc space-y-0.5 pl-5 text-xs text-amber-800">
                          {run.warnings.map((w, i) => <li key={i}>{w}</li>)}
                        </ul>
                      )}
                      <FileList run={run} />
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function FileList({ run }: { run: RbacExportRun }) {
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);

  const download = async (file: RbacExportFile) => {
    setBusy(file.id);
    setErrors((e) => ({ ...e, [file.id]: '' }));
    try {
      await api.downloadRbacExportFile(run.id, file.id);
    } catch (e) {
      setErrors((prev) => ({ ...prev, [file.id]: errorMessage(e, 'Download failed') }));
    } finally {
      setBusy(null);
    }
  };

  if (run.files.length === 0) return <p className="text-xs text-gray-500">No files in this run.</p>;
  return (
    <table className="w-full rounded border border-gray-200 bg-white text-xs">
      <thead className="border-b text-left text-gray-500">
        <tr>
          <th scope="col" className="px-2 py-1.5 font-medium">Sheet</th>
          <th scope="col" className="px-2 py-1.5 font-medium">Rows</th>
          <th scope="col" className="px-2 py-1.5 font-medium">Size</th>
          <th scope="col" className="px-2 py-1.5 font-medium">SHA-256</th>
          <th scope="col" className="px-2 py-1.5 font-medium">Status</th>
          <th scope="col" className="px-2 py-1.5 font-medium"><span className="sr-only">Download</span></th>
        </tr>
      </thead>
      <tbody>
        {run.files.map((f) => {
          const st = FILE_STATUS[f.status] ?? { label: f.status, tone: 'gray' as Tone };
          return (
            <tr key={f.id} className="border-b align-top last:border-b-0">
              <td className="px-2 py-1.5">
                <div className="font-mono text-gray-900">{f.sheet}</div>
                {f.path && <div className="break-all text-gray-400">{f.path}</div>}
              </td>
              <td className="px-2 py-1.5 tabular-nums">{f.row_count.toLocaleString()}</td>
              <td className="whitespace-nowrap px-2 py-1.5 tabular-nums">{formatBytes(f.size_bytes)}</td>
              <td className="px-2 py-1.5">
                {f.sha256 ? (
                  <span className="inline-flex items-center gap-1.5">
                    <code title={f.sha256}>{f.sha256.slice(0, 12)}…</code>
                    <CopyButton value={f.sha256} label="Copy SHA-256" />
                  </span>
                ) : (
                  <span className="text-gray-400">—</span>
                )}
              </td>
              <td className="px-2 py-1.5">
                <Badge tone={st.tone}>{st.label}</Badge>
                {f.error && <div className="mt-1 text-red-700">{f.error}</div>}
              </td>
              <td className="px-2 py-1.5 text-right">
                {f.downloadable && (
                  <Button size="sm" variant="outline" className="h-7 gap-1 px-2 text-xs" disabled={busy === f.id} onClick={() => download(f)}>
                    {busy === f.id ? <Loader2 className="h-3 w-3 animate-spin" /> : <Download className="h-3 w-3" />} Download
                  </Button>
                )}
                {errors[f.id] && <div className="mt-1 text-red-700">{errors[f.id]}</div>}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
