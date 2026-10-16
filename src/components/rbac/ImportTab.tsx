import { useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { AlertTriangle, FileSpreadsheet, Loader2 } from 'lucide-react';
import { api } from '../../services/api';
import type { RbacFinding, RbacImportPreview, RbacImportResult, RbacPermissions } from '../../types/rbac';
import { Button } from '../ui/button';
import { Card, CardContent } from '../ui/card';
import { Input } from '../ui/input';
import { FindingList } from './FindingList';
import { Badge, ErrorBanner, Notice, SectionTitle, Skeleton, type Tone, type ToastMessage } from './ui';
import { useLoad } from './useLoad';
import { PLANE_GROUP_LABELS, SETTINGS_PATH, errorMessage, formatTime, plural } from './utils';

const loadImports = () => api.getRbacImports();

const IMPORT_STATUS: Record<string, Tone> = {
  completed: 'green',
  succeeded: 'green',
  previewed: 'gray',
  running: 'blue',
  failed: 'red',
};

export function ImportTab({ permissions, notify, onOpenHistory }: {
  permissions: RbacPermissions;
  notify: (toast: Omit<ToastMessage, 'id'>) => void;
  onOpenHistory: () => void;
}) {
  const imports = useLoad(loadImports);
  const [file, setFile] = useState<File | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [preview, setPreview] = useState<RbacImportPreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [result, setResult] = useState<RbacImportResult | null>(null);

  const chosen = useRef<File | null>(null);

  const choose = async (next: File | null) => {
    chosen.current = next;
    setFile(next);
    setPreview(null);
    setResult(null);
    setPreviewError(null);
    if (!next) return;
    setPreviewing(true);
    try {
      const p = await api.previewRbacImport(next);
      if (chosen.current === next) setPreview(p);
    } catch (e) {
      if (chosen.current === next) setPreviewError(errorMessage(e, 'Could not read the workbook'));
    } finally {
      if (chosen.current === next) setPreviewing(false);
    }
  };

  return (
    <div className="space-y-6">
      <Card>
        <CardContent className="space-y-4 pt-5">
          <div>
            <h3 className="flex items-center gap-2 text-base font-semibold text-gray-900">
              <FileSpreadsheet className="h-4 w-4" /> Seed import of the RBAC workbook
            </h3>
            <p className="mt-1 max-w-3xl text-sm text-gray-500">
              This loads the workbook once, as the starting point. From then on the rules are edited here, History
              replaces the red-text convention, and the workbook is retired. Every managed sheet is read, hidden
              columns included, and nothing changes until you confirm the preview. Rows that break today’s rules are
              loaded and flagged, never dropped.
            </p>
          </div>
          <div>
            <label htmlFor="rbac-import-file" className="mb-1 block text-xs font-medium text-gray-600">Workbook (.xlsx)</label>
            <input
              id="rbac-import-file"
              type="file"
              accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              onChange={(e) => choose(e.target.files?.[0] ?? null)}
              className="block text-sm text-gray-700 file:mr-3 file:rounded-md file:border file:border-gray-300 file:bg-white file:px-3 file:py-1.5 file:text-sm hover:file:bg-gray-50"
            />
          </div>
          {previewing && (
            <span className="inline-flex items-center gap-2 text-sm text-gray-500">
              <Loader2 className="h-4 w-4 animate-spin" /> Reading the workbook…
            </span>
          )}
          {previewError && <ErrorBanner message={previewError} onRetry={file ? () => choose(file) : undefined} />}
        </CardContent>
      </Card>

      {result && <ImportResultView result={result} onOpenHistory={onOpenHistory} />}

      {preview && file && !result && (
        <ImportPreviewView
          preview={preview}
          file={file}
          canReset={permissions.can_reset}
          onImported={(r) => {
            setResult(r);
            setPreview(null);
            imports.reload();
            notify({ text: `Imported ${plural(r.created, 'row')}${r.deleted ? `, replacing ${plural(r.deleted, 'row')}` : ''}.` });
          }}
        />
      )}

      <section>
        <SectionTitle>Recent imports</SectionTitle>
        {imports.error ? (
          <ErrorBanner message={errorMessage(imports.error, 'Could not load past imports')} onRetry={imports.reload} />
        ) : !imports.data ? (
          <Skeleton rows={3} />
        ) : imports.data.length === 0 ? (
          <p className="text-sm text-gray-500">No imports yet.</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
            <table className="w-full text-sm">
              <thead className="border-b bg-gray-50 text-left text-xs text-gray-500">
                <tr>
                  <th scope="col" className="px-3 py-2 font-medium">Started</th>
                  <th scope="col" className="px-3 py-2 font-medium">File</th>
                  <th scope="col" className="px-3 py-2 font-medium">Mode</th>
                  <th scope="col" className="px-3 py-2 font-medium">Status</th>
                  <th scope="col" className="px-3 py-2 font-medium">By</th>
                  <th scope="col" className="px-3 py-2 font-medium">Completed</th>
                </tr>
              </thead>
              <tbody>
                {imports.data.map((imp) => (
                  <tr key={imp.id} className="border-b align-top last:border-b-0">
                    <td className="whitespace-nowrap px-3 py-2">{formatTime(imp.created_at)}</td>
                    <td className="px-3 py-2 text-gray-700">{imp.filename || '—'}</td>
                    <td className="px-3 py-2 text-gray-700">{imp.mode === 'reset' ? 'Reset and re-import' : imp.mode === 'initial' ? 'Initial' : imp.mode || '—'}</td>
                    <td className="px-3 py-2">
                      <Badge tone={IMPORT_STATUS[imp.status] ?? 'gray'}>{imp.status}</Badge>
                      {imp.error && <div className="mt-1 text-xs text-red-700">{imp.error}</div>}
                    </td>
                    <td className="px-3 py-2 text-gray-700">{imp.actor || '—'}</td>
                    <td className="whitespace-nowrap px-3 py-2">{formatTime(imp.completed_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function ImportPreviewView({ preview, file, canReset, onImported }: {
  preview: RbacImportPreview;
  file: File;
  canReset: boolean;
  onImported: (result: RbacImportResult) => void;
}) {
  const [confirmation, setConfirmation] = useState('');
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const totalRows = preview.sheets.reduce((n, s) => n + s.rows, 0);
  const reset = preview.mode === 'reset';
  const phrase = preview.confirmation_phrase ?? '';
  const resetBlocked = reset && (!canReset || !preview.reset_allowed || preview.in_flight_requests > 0);
  const confirmed = !reset || (phrase.length > 0 && confirmation === phrase);
  const canRun = !running && !resetBlocked && confirmed;

  const run = async () => {
    setRunning(true);
    setError(null);
    try {
      onImported(await api.runRbacImport(file, preview.import_id, preview.mode, reset ? confirmation : ''));
    } catch (e) {
      setError(errorMessage(e, 'Import failed'));
    } finally {
      setRunning(false);
    }
  };

  const findingContext = (f: RbacFinding) =>
    [f.sheet, f.row_number ? `row ${f.row_number}` : null].filter(Boolean).join(', ');

  return (
    <Card>
      <CardContent className="space-y-5 pt-5">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-gray-600">
          <span className="font-semibold text-gray-900">Preview: {preview.filename || file.name}</span>
          <code className="text-xs text-gray-400" title={preview.file_sha256}>sha256 {preview.file_sha256.slice(0, 12)}…</code>
          <span>{plural(totalRows, 'row')} in {plural(preview.sheets.length, 'sheet')}</span>
          <Badge tone={reset ? 'red' : 'blue'}>{reset ? 'Reset and re-import' : 'Initial import'}</Badge>
          {reset && <span>{plural(preview.existing_rules, 'rule')} in the app today</span>}
        </div>

        <div className="overflow-x-auto rounded-lg border border-gray-200">
          <table className="w-full text-sm">
            <thead className="border-b bg-gray-50 text-left text-xs text-gray-500">
              <tr>
                <th scope="col" className="px-3 py-2 font-medium">Sheet</th>
                <th scope="col" className="px-3 py-2 font-medium">Type</th>
                <th scope="col" className="px-3 py-2 font-medium">Environment</th>
                <th scope="col" className="px-3 py-2 font-medium">Rows</th>
                <th scope="col" className="px-3 py-2 font-medium">Headers as captured</th>
                <th scope="col" className="px-3 py-2 font-medium">Unknown headers</th>
                <th scope="col" className="px-3 py-2 font-medium">Duplicates</th>
                <th scope="col" className="px-3 py-2 font-medium">Warnings</th>
              </tr>
            </thead>
            <tbody>
              {preview.sheets.map((s) => (
                <tr key={s.sheet} className="border-b align-top last:border-b-0">
                  <td className="px-3 py-2 font-mono text-xs text-gray-900">{s.sheet}</td>
                  <td className="whitespace-nowrap px-3 py-2 text-gray-700">{PLANE_GROUP_LABELS[s.plane] ?? s.plane}</td>
                  <td className="px-3 py-2 text-gray-700">{s.environment || '—'}</td>
                  <td className="px-3 py-2 tabular-nums">{s.rows.toLocaleString()}</td>
                  <td className="px-3 py-2">
                    <div className="flex flex-wrap gap-1">
                      {s.columns.map((c, i) => (
                        <code
                          key={i}
                          title={c.field ? `Read as ${c.field}` : 'Not a modelled column: kept as is'}
                          className={`rounded px-1 text-xs ${c.field ? 'bg-gray-100 text-gray-700' : 'bg-amber-50 text-amber-800'}`}
                        >
                          {c.header}
                        </code>
                      ))}
                    </div>
                  </td>
                  <td className="px-3 py-2 text-xs">
                    {s.unknown_headers.length ? <span className="text-amber-800">{s.unknown_headers.join(', ')}</span> : <span className="text-gray-400">—</span>}
                  </td>
                  <td className="px-3 py-2">{s.duplicates ? <Badge tone="amber">{s.duplicates}</Badge> : <span className="text-gray-400">0</span>}</td>
                  <td className="px-3 py-2">{s.warnings ? <Badge tone="amber">{s.warnings}</Badge> : <span className="text-gray-400">0</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {(preview.skipped_sheets.length > 0 || preview.missing_sheets.length > 0) && (
          <div className="grid gap-3 md:grid-cols-2">
            {preview.skipped_sheets.length > 0 && (
              <Notice kind="info" title="Tabs skipped (not managed sheets)">
                {preview.skipped_sheets.join(', ')}
              </Notice>
            )}
            {preview.missing_sheets.length > 0 && (
              <Notice kind="warn" title="Managed sheets missing from the workbook">
                {preview.missing_sheets.join(', ')}. They stay empty and aren’t exported until they have rows.
              </Notice>
            )}
          </div>
        )}

        {preview.findings.length > 0 && (
          <div>
            <SectionTitle>Findings</SectionTitle>
            <p className="mb-2 text-xs text-gray-500">
              Imports are never blocked: these rows load as they are, flagged so you can fix them later on the Rules tab.
            </p>
            <FindingList
              findings={preview.findings}
              total={preview.findings_total}
              context={findingContext}
              denyHint="Would be refused as a new change, but imports load them anyway."
            />
          </div>
        )}

        {reset ? (
          <div className="space-y-3 rounded-lg border border-red-300 bg-red-50 p-4">
            <p className="flex items-start gap-2 text-sm text-red-800">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>
                <strong>This replaces every rule.</strong> All {plural(preview.existing_rules, 'current rule')} are
                deleted and the workbook is loaded in their place, discarding every change made in the app since the
                seed import. It’s recorded as one change set, so it can be reverted from History, and the next export
                reflects it.
              </span>
            </p>
            {!canReset && <p className="text-sm text-red-800">Only Platform Admins can reset and re-import.</p>}
            {!preview.reset_allowed && (
              <p className="text-sm text-red-800">
                Reset is turned off. A Platform Admin can allow it with “Allow reset” in{' '}
                <Link to={SETTINGS_PATH} className="font-medium underline">Admin → Settings → Access Rules</Link>; it
                should stay off in production.
              </p>
            )}
            {preview.in_flight_requests > 0 && (
              <p className="text-sm text-red-800">
                {plural(preview.in_flight_requests, 'access request is', 'access requests are')} still in flight. Finish
                or reject them first: they were planned against the current rows, which a reset would replace.
              </p>
            )}
            {!resetBlocked && !phrase && (
              <p className="text-sm text-red-800">The server didn’t send a confirmation phrase, so the reset can’t be confirmed. Preview the file again.</p>
            )}
            {!resetBlocked && phrase && (
              <div>
                <label htmlFor="rbac-reset-confirm" className="mb-1 block text-sm text-red-900">
                  Type <code className="rounded bg-white px-1 font-semibold">{phrase}</code> to confirm
                </label>
                <Input
                  id="rbac-reset-confirm"
                  value={confirmation}
                  onChange={(e) => setConfirmation(e.target.value)}
                  autoComplete="off"
                  className="max-w-md bg-white"
                />
              </div>
            )}
            <Button onClick={run} disabled={!canRun} className="bg-red-600 hover:bg-red-700">
              {running && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Replace all rules with {plural(totalRows, 'row')}
            </Button>
          </div>
        ) : (
          <div className="flex items-center gap-3">
            <Button onClick={run} disabled={!canRun}>
              {running && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Import {plural(totalRows, 'row')}
            </Button>
            <span className="text-xs text-gray-500">Recorded in History as one change set.</span>
          </div>
        )}
        {error && <ErrorBanner message={error} />}
      </CardContent>
    </Card>
  );
}

function ImportResultView({ result, onOpenHistory }: { result: RbacImportResult; onOpenHistory: () => void }) {
  return (
    <Notice kind="success" title="Import complete">
      <p>
        {plural(result.created, 'row')} loaded
        {result.deleted ? `, ${plural(result.deleted, 'previous row')} replaced` : ''} across{' '}
        {plural(result.sheets.length, 'sheet')}.
        {result.change_set_id && (
          <>
            {' '}
            <button type="button" onClick={onOpenHistory} className="font-medium underline">See it in History</button>.
          </>
        )}
      </p>
      {result.sheets.length > 0 && (
        <ul className="mt-1 flex flex-wrap gap-x-4 text-xs">
          {result.sheets.map((s) => (
            <li key={s.sheet}><span className="font-mono">{s.sheet}</span>: {s.rows.toLocaleString()}</li>
          ))}
        </ul>
      )}
    </Notice>
  );
}
