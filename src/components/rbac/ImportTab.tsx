import { useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { AlertTriangle, FileSpreadsheet, Loader2 } from 'lucide-react';
import { api } from '../../services/api';
import type { RbacFinding, RbacImportPreview, RbacImportResult, RbacPermissions } from '../../types/rbac';
import { Button } from '../ui/button';
import { Card, CardContent } from '../ui/card';
import { Input } from '../ui/input';
import { cn } from '../../lib/utils';
import { ColGroup, ResetColumnsButton, ResizableTh } from './ColumnResizer';
import { FindingList } from './FindingList';
import { Badge, ErrorBanner, Notice, SectionTitle, Skeleton, type Tone, type ToastMessage } from './ui';
import { useLoad } from './useLoad';
import { useResizableColumns } from './useResizableColumns';
import { CELL, PLANE_GROUP_LABELS, SETTINGS_PATH, TEXT_CELL, errorMessage, formatTime, plural } from './utils';

const loadImports = () => api.getRbacImports();

const IMPORT_COLUMNS = ['started', 'file', 'mode', 'status', 'by', 'completed'];
const PREVIEW_COLUMNS = ['sheet', 'type', 'environment', 'rows', 'headers', 'unknown', 'duplicates', 'warnings'];

const removedDuplicates = (r: { duplicates_removed: boolean; sheets: { duplicates: number }[] }) =>
  r.duplicates_removed ? r.sheets.reduce((n, s) => n + s.duplicates, 0) : 0;

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
  const { attachTable, ...widths } = useResizableColumns('imports', IMPORT_COLUMNS);

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
    <div className="space-y-4">
      <Card>
        <CardContent className="space-y-4 pt-5">
          <div>
            <h3 className="flex items-center gap-2 text-base font-semibold text-gray-900">
              <FileSpreadsheet className="h-4 w-4" /> Seed import of the RBAC workbook
            </h3>
            <p className="mt-1 max-w-3xl text-sm text-gray-500">
              This loads the workbook once, as the starting point. From then on the rules are edited here, History
              replaces the red-text convention, and the workbook is retired. Every managed sheet is read, hidden
              columns included, and nothing changes until you confirm the preview. The workbook is the source of truth,
              so its rows are copied exactly as they are and aren’t checked against the access rules; those apply to
              changes made after the import.
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
            const removed = removedDuplicates(r);
            notify({
              text: `Imported ${plural(r.created, 'row')}${r.deleted ? `, replacing ${plural(r.deleted, 'row')}` : ''}`
                + `${removed ? ` and removing ${plural(removed, 'duplicate row')}` : ''}.`,
            });
          }}
        />
      )}

      <section>
        <SectionTitle actions={<ResetColumnsButton columns={widths} />}>Recent imports</SectionTitle>
        {imports.error ? (
          <ErrorBanner message={errorMessage(imports.error, 'Could not load past imports')} onRetry={imports.reload} />
        ) : !imports.data ? (
          <Skeleton rows={3} />
        ) : imports.data.length === 0 ? (
          <p className="text-[13px] text-gray-500">No imports yet.</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
            <table ref={attachTable} style={widths.tableStyle} className={cn('text-[13px]', !widths.active && 'w-full')}>
              <ColGroup columns={widths} />
              <thead className="border-b bg-gray-50 text-left text-xs text-gray-500">
                <tr>
                  <ResizableTh columns={widths} id="started" label="Started" />
                  <ResizableTh columns={widths} id="file" label="File" />
                  <ResizableTh columns={widths} id="mode" label="Mode" />
                  <ResizableTh columns={widths} id="status" label="Status" />
                  <ResizableTh columns={widths} id="by" label="By" />
                  <ResizableTh columns={widths} id="completed" label="Completed" />
                </tr>
              </thead>
              <tbody>
                {imports.data.map((imp) => {
                  const mode = imp.mode === 'reset' ? 'Reset and re-import' : imp.mode === 'initial' ? 'Initial' : imp.mode || '—';
                  return (
                    <tr key={imp.id} className="border-b align-top last:border-b-0">
                      <td className={TEXT_CELL} title={formatTime(imp.created_at)}>{formatTime(imp.created_at)}</td>
                      <td className={cn(TEXT_CELL, 'text-gray-700')} title={imp.filename || undefined}>{imp.filename || '—'}</td>
                      <td className={cn(TEXT_CELL, 'text-gray-700')} title={mode}>{mode}</td>
                      <td className={CELL}>
                        <Badge tone={IMPORT_STATUS[imp.status] ?? 'gray'}>{imp.status}</Badge>
                        {imp.error && <div className="mt-1 line-clamp-2 break-words text-xs text-red-700" title={imp.error}>{imp.error}</div>}
                      </td>
                      <td className={cn(TEXT_CELL, 'text-gray-700')} title={imp.actor || undefined}>{imp.actor || '—'}</td>
                      <td className={TEXT_CELL} title={formatTime(imp.completed_at)}>{formatTime(imp.completed_at)}</td>
                    </tr>
                  );
                })}
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
  const { attachTable, ...widths } = useResizableColumns('import-preview', PREVIEW_COLUMNS);
  const totalRows = preview.sheets.reduce((n, s) => n + s.rows, 0);
  const removed = removedDuplicates(preview);
  const duplicatesLabel = preview.duplicates_removed ? 'Duplicates removed' : 'Duplicates';
  const duplicatesTip = preview.duplicates_removed
    ? 'Rows identical to an earlier row on the same sheet. They aren’t imported and aren’t counted in Rows. '
      + 'To keep them, turn off “Remove duplicate rows on import” in Admin → Settings → Access Rules and preview again.'
    : 'Rows identical to an earlier row on the same sheet. They’re kept and counted in Rows, so the export matches the workbook.';
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
          <span>
            {plural(totalRows, 'row')} in {plural(preview.sheets.length, 'sheet')}
            {removed > 0 && <>, after removing {plural(removed, 'duplicate row')}</>}
          </span>
          <Badge tone={reset ? 'red' : 'blue'}>{reset ? 'Reset and re-import' : 'Initial import'}</Badge>
          {reset && <span>{plural(preview.existing_rules, 'rule')} in the app today</span>}
        </div>

        <div className="space-y-1">
          <div className="flex justify-end">
            <ResetColumnsButton columns={widths} />
          </div>
          <div className="overflow-x-auto rounded-lg border border-gray-200">
            <table ref={attachTable} style={widths.tableStyle} className={cn('text-[13px]', !widths.active && 'w-full')}>
              <ColGroup columns={widths} />
              <thead className="border-b bg-gray-50 text-left text-xs text-gray-500">
                <tr>
                  <ResizableTh columns={widths} id="sheet" label="Sheet" />
                  <ResizableTh columns={widths} id="type" label="Type" />
                  <ResizableTh columns={widths} id="environment" label="Environment" />
                  <ResizableTh columns={widths} id="rows" label="Rows" />
                  <ResizableTh columns={widths} id="headers" label="Headers as captured" />
                  <ResizableTh columns={widths} id="unknown" label="Unknown headers" />
                  <ResizableTh columns={widths} id="duplicates" label={duplicatesLabel}>
                    <span title={duplicatesTip}>{duplicatesLabel}</span>
                  </ResizableTh>
                  <ResizableTh columns={widths} id="warnings" label="Warnings" />
                </tr>
              </thead>
              <tbody>
                {preview.sheets.map((s) => {
                  const plane = PLANE_GROUP_LABELS[s.plane] ?? s.plane;
                  return (
                    <tr key={s.sheet} className="border-b align-top last:border-b-0">
                      <td className={cn(TEXT_CELL, 'font-mono text-xs text-gray-900')} title={s.sheet}>{s.sheet}</td>
                      <td className={cn(TEXT_CELL, 'text-gray-700')} title={plane}>{plane}</td>
                      <td className={cn(TEXT_CELL, 'text-gray-700')} title={s.environment || undefined}>{s.environment || '—'}</td>
                      <td className={cn(TEXT_CELL, 'tabular-nums')}>{s.rows.toLocaleString()}</td>
                      <td className={CELL}>
                        <div className="flex flex-wrap gap-1">
                          {s.columns.map((c, i) => (
                            <code
                              key={i}
                              title={`${c.header}: ${c.field ? `read as ${c.field}` : 'not a modelled column, kept as is'}`}
                              className={cn('rounded px-1 text-xs', widths.active && 'max-w-full truncate', c.field ? 'bg-gray-100 text-gray-700' : 'bg-amber-50 text-amber-800')}
                            >
                              {c.header}
                            </code>
                          ))}
                        </div>
                      </td>
                      <td className={cn(CELL, 'break-words text-xs')}>
                        {s.unknown_headers.length ? <span className="line-clamp-2 text-amber-800" title={s.unknown_headers.join(', ')}>{s.unknown_headers.join(', ')}</span> : <span className="text-gray-400">—</span>}
                      </td>
                      <td className={CELL} title={s.duplicates ? duplicatesTip : undefined}>{s.duplicates ? <Badge tone="amber">{s.duplicates}</Badge> : <span className="text-gray-400">0</span>}</td>
                      <td className={CELL}>{s.warnings ? <Badge tone="amber">{s.warnings}</Badge> : <span className="text-gray-400">0</span>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
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
              {preview.duplicates_removed
                ? 'Things to know about the copy, such as an unrecognised column or an identical row. None of them '
                  + 'stops the import. Identical rows are removed, so the export won’t have them; everything else is '
                  + 'kept and the export reproduces it.'
                : 'Things the copy keeps from the workbook, such as an unrecognised column or an identical row. They '
                  + 'don’t stop the import, and the export reproduces them.'}
            </p>
            <FindingList findings={preview.findings} total={preview.findings_total} context={findingContext} />
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
                or reject them first: they were prepared against the current rows, which a reset would replace.
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
  const removed = removedDuplicates(result);
  return (
    <Notice kind="success" title="Import complete">
      <p>
        {plural(result.created, 'row')} loaded
        {result.deleted ? `, ${plural(result.deleted, 'previous row')} replaced` : ''} across{' '}
        {plural(result.sheets.length, 'sheet')}.
        {removed > 0 && ` ${plural(removed, 'duplicate row was', 'duplicate rows were')} removed.`}
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
            <li key={s.sheet}>
              <span className="font-mono">{s.sheet}</span>: {s.rows.toLocaleString()}
              {s.duplicates_removed && s.duplicates > 0 && ` (${s.duplicates.toLocaleString()} removed)`}
            </li>
          ))}
        </ul>
      )}
    </Notice>
  );
}
