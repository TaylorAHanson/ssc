import { useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Loader2, Users } from 'lucide-react';
import { api } from '../../services/api';
import type {
  RbacChangeSetResult,
  RbacFinding,
  RbacGrantImportLine,
  RbacGrantImportPreview,
  RbacGrantLineStatus,
  RbacPermissions,
} from '../../types/rbac';
import { Button } from '../ui/button';
import { Card, CardContent } from '../ui/card';
import { Textarea } from '../ui/textarea';
import { cn } from '../../lib/utils';
import { errorMessage } from '../../lib/errors';
import { DiffView } from './DiffView';
import { FindingList } from './FindingList';
import { Badge, ErrorBanner, Notice, SectionTitle, type Tone, type ToastMessage } from './ui';
import { useLoad } from './useLoad';
import { CELL, SETTINGS_PATH, TEXT_CELL, blockedPreviewOf, loadSheets, plural } from './utils';

const STATUS: Record<RbacGrantLineStatus, { label: string; tone: Tone }> = {
  change: { label: 'Will be written', tone: 'green' },
  already_held: { label: 'Already held', tone: 'gray' },
  covered: { label: 'Covered', tone: 'blue' },
  duplicate: { label: 'Duplicate', tone: 'gray' },
  rejected: { label: 'Rejected', tone: 'red' },
};

const NOT_WRITTEN: ReadonlySet<RbacGrantLineStatus> = new Set(['covered', 'duplicate', 'rejected']);
const PREREQUISITES_SHOWN = 20;

/** Bulk SELECT grants from a two-column CSV (securable, group), previewed then saved as one change set. */
export function GrantImportCard({ permissions, notify, onOpenHistory }: {
  permissions: RbacPermissions;
  notify: (toast: Omit<ToastMessage, 'id'>) => void;
  onOpenHistory: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [preview, setPreview] = useState<RbacGrantImportPreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [result, setResult] = useState<RbacChangeSetResult | null>(null);
  const chosen = useRef<File | null>(null);
  const input = useRef<HTMLInputElement | null>(null);

  const choose = async (next: File | null) => {
    chosen.current = next;
    setFile(next);
    setPreview(null);
    setResult(null);
    setPreviewError(null);
    if (!next) return;
    setPreviewing(true);
    try {
      const p = await api.previewRbacGrantImport(next);
      if (chosen.current === next) setPreview(p);
    } catch (e) {
      if (chosen.current === next) setPreviewError(errorMessage(e, 'Could not read the file'));
    } finally {
      if (chosen.current === next) setPreviewing(false);
    }
  };

  const onSaved = (r: RbacChangeSetResult) => {
    setResult(r);
    setPreview(null);
    if (input.current) input.current.value = '';
    if (r.request_id) {
      notify({
        text: 'Request filed. Another approver must approve it before the rows change.',
        link: { to: `/requests/${r.request_id}`, label: 'View request' },
      });
    } else {
      notify({ text: `Saved ${plural(r.diffs.length, 'row change')}. It’s in History and goes out with the next export.` });
    }
  };

  return (
    <Card>
      <CardContent className="space-y-4 pt-5">
        <div>
          <h3 className="flex items-center gap-2 text-base font-semibold text-gray-900">
            <Users className="h-4 w-4" /> Bulk SELECT grants (CSV)
          </h3>
          <p className="mt-1 max-w-3xl text-sm text-gray-500">
            Grant read access to many groups at once. Column A names a schema (<code>catalog.schema</code> or{' '}
            <code>catalog.schema.*</code>) or a view (<code>catalog.schema.view</code>); column B names one group. A
            header row is optional. Each line grants <code>SELECT</code>; <code>USE_CATALOG</code> and{' '}
            <code>USE_SCHEMA</code> are added where the group lacks them, and groups are added to existing rows where
            one fits. The environment comes from the catalog name, or from the catalog lists in{' '}
            <Link to={SETTINGS_PATH} className="text-accent hover:underline">Admin → Settings → Access Rules</Link>.
            Nothing changes until you save the preview.
          </p>
        </div>
        <div>
          <label htmlFor="rbac-grant-file" className="mb-1 block text-xs font-medium text-gray-600">Grants (.csv)</label>
          <input
            ref={input}
            id="rbac-grant-file"
            type="file"
            accept=".csv,text/csv"
            onChange={(e) => choose(e.target.files?.[0] ?? null)}
            className="block text-sm text-gray-700 file:mr-3 file:rounded-md file:border file:border-gray-300 file:bg-white file:px-3 file:py-1.5 file:text-sm hover:file:bg-gray-50"
          />
        </div>
        {previewing && (
          <span className="inline-flex items-center gap-2 text-sm text-gray-500">
            <Loader2 className="h-4 w-4 animate-spin" /> Reading the file…
          </span>
        )}
        {previewError && <ErrorBanner message={previewError} onRetry={file ? () => choose(file) : undefined} />}
        {result && (
          <Notice kind="success" title={result.request_id ? 'Request filed' : 'Grants saved'}>
            {result.request_id ? (
              <>
                The two-person rule applies, so the {plural(result.diffs.length, 'row change')} wait for another
                approver.{' '}
                <Link to={`/requests/${result.request_id}`} className="font-medium underline">View the request</Link>.
              </>
            ) : (
              <>
                {plural(result.diffs.length, 'row change')} saved as one change set.{' '}
                <button type="button" onClick={onOpenHistory} className="font-medium underline">See it in History</button>.
              </>
            )}
          </Notice>
        )}
        {preview && file && !result && (
          <GrantPreview
            preview={preview}
            file={file}
            minReasonLength={permissions.min_reason_length}
            onSaved={onSaved}
            onPreviewAgain={() => choose(file)}
          />
        )}
      </CardContent>
    </Card>
  );
}

function GrantPreview({ preview, file, minReasonLength, onSaved, onPreviewAgain }: {
  preview: RbacGrantImportPreview;
  file: File;
  minReasonLength: number;
  onSaved: (result: RbacChangeSetResult) => void;
  onPreviewAgain: () => void;
}) {
  const sheets = useLoad(loadSheets);
  const [reason, setReason] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [onlyFlagged, setOnlyFlagged] = useState(() => preview.lines.some((l) => NOT_WRITTEN.has(l.status)));
  const [showDiffs, setShowDiffs] = useState(false);
  const [showAllPrereqs, setShowAllPrereqs] = useState(false);

  const counts = useMemo(() => {
    const out: Record<RbacGrantLineStatus, number> = { change: 0, already_held: 0, covered: 0, duplicate: 0, rejected: 0 };
    preview.lines.forEach((l) => { out[l.status] += 1; });
    return out;
  }, [preview.lines]);
  const flagged = preview.lines.filter((l) => NOT_WRITTEN.has(l.status));
  const shownLines = onlyFlagged && flagged.length > 0 ? flagged : preview.lines;
  const rowChanges = preview.diffs.length;
  const sheetNames = [...new Set(preview.diffs.map((d) => d.sheet))];

  const blockedByCommit = blockedPreviewOf(error);
  const findings = blockedByCommit?.findings ?? preview.findings;
  const reasonShort = reason.trim().length < Math.max(minReasonLength, 1);
  const canSave = !preview.blocked && rowChanges > 0 && !reasonShort && !saving;
  const stale = !!error && errorMessage(error, '').includes('changed since the preview');

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      onSaved(await api.runRbacGrantImport(file, preview.fingerprint, reason.trim()));
    } catch (e) {
      setError(e);
    } finally {
      setSaving(false);
    }
  };

  const findingContext = (f: RbacFinding) => f.sheet ?? '';
  const prereqs = showAllPrereqs ? preview.prerequisites : preview.prerequisites.slice(0, PREREQUISITES_SHOWN);

  return (
    <div className="space-y-5 border-t pt-4">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-gray-600">
        <span className="font-semibold text-gray-900">Preview: {preview.filename || file.name}</span>
        <span>
          {plural(preview.lines.length, 'line')}
          {preview.header_skipped && ' (header row skipped)'}, {plural(rowChanges, 'row change')}
          {sheetNames.length > 0 && <> on {sheetNames.map((s, i) => <span key={s}>{i > 0 && ', '}<code className="text-xs">{s}</code></span>)}</>}
        </span>
      </div>

      <div className="flex flex-wrap gap-2">
        {(Object.keys(STATUS) as RbacGrantLineStatus[]).filter((s) => counts[s] > 0).map((s) => (
          <Badge key={s} tone={STATUS[s].tone}>{STATUS[s].label}: {counts[s]}</Badge>
        ))}
      </div>

      {preview.blocked_reasons.length > 0 && (
        <Notice kind="error" title="Can’t save yet">
          <ul className="list-disc pl-4">
            {preview.blocked_reasons.map((r) => <li key={r}>{r}</li>)}
          </ul>
        </Notice>
      )}
      {counts.covered > 0 && (
        <Notice kind="info">
          {plural(counts.covered, 'view line is', 'view lines are')} covered by a <code>SELECT</code> the same group
          already has (or gets in this file) on the schema or catalog. They aren’t written, since the view is readable
          already.
        </Notice>
      )}

      <section>
        <SectionTitle
          actions={flagged.length > 0 ? (
            <label className="flex items-center gap-1.5 text-xs text-gray-600">
              <input type="checkbox" checked={onlyFlagged} onChange={(e) => setOnlyFlagged(e.target.checked)} />
              Only lines that aren’t written ({flagged.length})
            </label>
          ) : undefined}
        >
          Lines
        </SectionTitle>
        <div className="max-h-96 overflow-auto rounded-lg border border-gray-200">
          <table className="w-full text-[13px]">
            <thead className="sticky top-0 border-b bg-gray-50 text-left text-xs text-gray-500">
              <tr>
                <th className="px-2 py-1.5 font-medium">Line</th>
                <th className="px-2 py-1.5 font-medium">Securable</th>
                <th className="px-2 py-1.5 font-medium">Group</th>
                <th className="px-2 py-1.5 font-medium">Environment</th>
                <th className="px-2 py-1.5 font-medium">Outcome</th>
                <th className="px-2 py-1.5 font-medium">Details</th>
              </tr>
            </thead>
            <tbody>
              {shownLines.map((l) => <LineRow key={l.line} line={l} />)}
            </tbody>
          </table>
        </div>
      </section>

      {preview.prerequisites.length > 0 && (
        <section>
          <SectionTitle>Prerequisites added ({preview.prerequisites.length})</SectionTitle>
          <ul className="space-y-0.5 font-mono text-xs text-gray-700">
            {prereqs.map((p) => <li key={p}>{p}</li>)}
          </ul>
          {preview.prerequisites.length > PREREQUISITES_SHOWN && (
            <Button size="sm" variant="outline" className="mt-2" onClick={() => setShowAllPrereqs((v) => !v)}>
              {showAllPrereqs ? 'Show fewer' : `Show all ${preview.prerequisites.length}`}
            </Button>
          )}
        </section>
      )}

      {findings.length > 0 && (
        <section>
          <SectionTitle>Findings</SectionTitle>
          <FindingList findings={findings} context={findingContext} />
        </section>
      )}

      {rowChanges > 0 && (
        <section>
          <SectionTitle
            actions={
              <Button size="sm" variant="outline" onClick={() => setShowDiffs((v) => !v)}>
                {showDiffs ? 'Hide' : 'Show'} row changes
              </Button>
            }
          >
            Row changes ({rowChanges})
          </SectionTitle>
          {showDiffs && <DiffView diffs={preview.diffs} sheets={sheets.data ?? []} />}
        </section>
      )}

      {rowChanges === 0 && !preview.blocked ? (
        <Notice kind="info">Nothing to save: every grant in the file is already in the rules.</Notice>
      ) : (
        <div className="space-y-3">
          {preview.two_person_required && (
            <Notice kind="warn">
              An environment in this file has the two-person rule on, so saving files a request for another approver
              instead of changing the rows now.
            </Notice>
          )}
          <div>
            <label htmlFor="rbac-grant-reason" className="mb-1 block text-sm font-medium text-gray-700">Reason</label>
            <Textarea
              id="rbac-grant-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={2}
              placeholder="Why these groups need read access, for History and the audit record"
              className="max-w-2xl"
            />
            {minReasonLength > 1 && reasonShort && (
              <p className="mt-1 text-xs text-gray-500">At least {minReasonLength} characters.</p>
            )}
          </div>
          <div className="flex items-center gap-3">
            <Button onClick={save} disabled={!canSave}>
              {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {preview.two_person_required ? 'File request for' : 'Save'} {plural(rowChanges, 'row change')}
            </Button>
            <span className="text-xs text-gray-500">Recorded in History as one change set, all or nothing.</span>
          </div>
        </div>
      )}
      {!!error && !blockedByCommit && (
        <ErrorBanner message={errorMessage(error, 'Saving failed')} onRetry={stale ? onPreviewAgain : undefined} />
      )}
      {blockedByCommit && <ErrorBanner message="Saving was blocked by an access policy. See the findings above." />}
    </div>
  );
}

function LineRow({ line }: { line: RbacGrantImportLine }) {
  const status = STATUS[line.status];
  return (
    <tr className="border-b align-top last:border-b-0">
      <td className={cn(TEXT_CELL, 'tabular-nums text-gray-500')}>{line.line}</td>
      <td className={cn(CELL, 'break-all font-mono text-xs text-gray-900')}>{line.securable || '—'}</td>
      <td className={cn(CELL, 'break-all font-mono text-xs text-gray-700')}>{line.group || '—'}</td>
      <td className={cn(TEXT_CELL, 'text-gray-700')}>{line.environment || '—'}</td>
      <td className={CELL}><Badge tone={status.tone}>{status.label}</Badge></td>
      <td className={cn(CELL, 'text-xs text-gray-600', line.status === 'rejected' && 'text-red-700')}>{line.message || '—'}</td>
    </tr>
  );
}
