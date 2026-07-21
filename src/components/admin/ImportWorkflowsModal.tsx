import { useRef, useState } from 'react';
import { AlertTriangle, CheckCircle2, Loader2, Upload, X } from 'lucide-react';
import { Button } from '../ui/button';
import { api } from '../../services/api';
import type { ImportReport, WorkflowBundle } from '../../services/api';

interface Props {
  onImported: () => void;
  onClose: () => void;
  /** When true (locked env, e.g. prod), import is the only way to change workflows,
   *  so default to publishing on import — there's no in-place publish step here. */
  locked?: boolean;
}

/** Inbound half of env promotion: paste/upload a bundle exported from another
 *  environment and upsert it here. Defaults to importing as drafts so the
 *  promoted workflows are reviewed and dry-run tested before being published —
 *  except in a locked environment, where it defaults to published. */
export function ImportWorkflowsModal({ onImported, onClose, locked }: Props) {
  const [text, setText] = useState('');
  const [asStatus, setAsStatus] = useState<'draft' | 'published'>(locked ? 'published' : 'draft');
  const [overwrite, setOverwrite] = useState(true);
  const [prune, setPrune] = useState(false);
  const [confirmPrune, setConfirmPrune] = useState(false);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<ImportReport | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const onFile = async (file: File) => {
    setText(await file.text());
    setError(null);
  };

  // Clicking Import: prune is destructive, so require an extra confirmation popup first.
  const onImportClick = () => {
    setError(null);
    if (!text.trim()) return;
    if (prune) {
      setConfirmPrune(true);
      return;
    }
    void run();
  };

  const run = async () => {
    setConfirmPrune(false);
    let bundle: WorkflowBundle;
    try {
      bundle = JSON.parse(text);
    } catch {
      setError('Pasted content is not valid JSON.');
      return;
    }
    setImporting(true);
    setError(null);
    setReport(null);
    try {
      const r = await api.importWorkflowsBundle(bundle, { asStatus, overwrite, prune });
      setReport(r);
      onImported();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Import failed');
    } finally {
      setImporting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg max-h-[88vh] flex flex-col">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200">
          <div className="flex items-center gap-2">
            <Upload className="w-4 h-4 text-accent" />
            <h2 className="text-sm font-semibold">Import workflows</h2>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-5 space-y-3 overflow-y-auto">
          <p className="text-xs text-gray-500">
            Paste a bundle exported from another environment, or load a <code>.json</code> file.
            Workflows are matched by key.{' '}
            {locked
              ? 'This environment is locked, so import is the promotion path — bundles import as published.'
              : 'Imported as drafts by default — review and dry-run them here before publishing.'}
          </p>

          <div className="flex items-center gap-2">
            <input
              ref={fileRef}
              type="file"
              accept="application/json,.json"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) onFile(f);
              }}
            />
            <Button variant="outline" size="sm" onClick={() => fileRef.current?.click()}>
              <Upload className="w-3.5 h-3.5 mr-1" /> Load file
            </Button>
          </div>

          <textarea
            className="w-full h-40 border border-gray-300 rounded-md p-2 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-accent"
            placeholder='{ "format": "selfservice.workflows/v1", "workflows": [ ... ] }'
            value={text}
            onChange={(e) => setText(e.target.value)}
          />

          <div className="flex flex-wrap items-center gap-4">
            <label className="text-xs text-gray-600 flex items-center gap-1.5">
              Import as
              <select
                className="border border-gray-300 rounded-md h-8 px-2 text-xs"
                value={asStatus}
                onChange={(e) => setAsStatus(e.target.value as 'draft' | 'published')}
              >
                <option value="draft">draft (recommended)</option>
                <option value="published">published</option>
              </select>
            </label>
            <label className="text-xs text-gray-600 inline-flex items-center gap-1.5">
              <input type="checkbox" checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} />
              Overwrite workflows with the same key
            </label>
            <label className="text-xs text-gray-600 inline-flex items-center gap-1.5">
              <input type="checkbox" checked={prune} onChange={(e) => setPrune(e.target.checked)} />
              Delete workflows not in this bundle
            </label>
          </div>

          {prune && (
            <div className="text-[11px] text-red-800 bg-red-50 border border-red-200 rounded-md px-3 py-2 flex items-start gap-2">
              <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
              <span>
                <strong>Destructive.</strong> Every workflow in <em>this</em> environment whose key is not in the
                bundle will be permanently deleted — <strong>including built-in seeded workflows</strong>, which are
                also tombstoned so they don't get re-created on the next restart. Use this only to propagate
                deletions from the source environment, and make sure this bundle is the full export.
              </span>
            </div>
          )}

          {asStatus === 'published' && !locked && (
            <div className="text-[11px] text-amber-800 bg-amber-50 border border-amber-200 rounded-md px-3 py-2 flex items-start gap-2">
              <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
              Importing directly as published skips the review/test step. Prefer draft for promotions.
            </div>
          )}

          {error && (
            <div className="text-xs text-red-600 bg-red-50 border border-red-200 rounded-md px-3 py-2 flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" /> {error}
            </div>
          )}

          {report && (
            <div className="text-xs border border-gray-200 rounded-md divide-y divide-gray-100">
              <div className="px-3 py-1.5 flex items-center gap-2 text-green-700">
                <CheckCircle2 className="w-3.5 h-3.5" />
                {report.created.length} created · {report.updated.length} updated · {report.skipped.length} skipped
                {report.pruned && report.pruned.length > 0 && (
                  <span className="text-red-700"> · {report.pruned.length} deleted</span>
                )}
              </div>
              {(report.created.length > 0 || report.updated.length > 0) && (
                <div className="px-3 py-1.5 text-gray-600">
                  {[...report.created, ...report.updated].join(', ')}
                </div>
              )}
              {report.pruned && report.pruned.length > 0 && (
                <div className="px-3 py-1.5 text-red-700">
                  Deleted: {report.pruned.join(', ')}
                </div>
              )}
              {report.errors.length > 0 && (
                <div className="px-3 py-1.5 text-red-600">
                  {report.errors.map((e, i) => (
                    <div key={i}>{e.key}: {e.error}</div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-gray-200">
          <Button variant="outline" onClick={onClose} disabled={importing}>
            {report ? 'Done' : 'Cancel'}
          </Button>
          <Button onClick={onImportClick} disabled={importing || !text.trim()}>
            {importing ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Upload className="w-4 h-4 mr-1" />}
            Import
          </Button>
        </div>
      </div>

      {confirmPrune && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-4">
          <div className="bg-white rounded-xl shadow-xl w-full max-w-md">
            <div className="flex items-center gap-2 px-5 py-3 border-b border-gray-200">
              <AlertTriangle className="w-4 h-4 text-red-600" />
              <h3 className="text-sm font-semibold text-red-700">Confirm delete on import</h3>
            </div>
            <div className="px-5 py-4 text-sm text-gray-700 space-y-2">
              <p>
                You enabled <strong>Delete workflows not in this bundle</strong>. Importing will
                permanently delete every workflow in this environment whose key is not in the bundle
                — <strong>including built-in seeded workflows</strong> (they're tombstoned so a restart
                won't re-create them). This cannot be undone.
              </p>
              <p className="text-xs text-gray-500">
                Make sure this bundle is the complete export from the source environment.
              </p>
            </div>
            <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-gray-200">
              <Button variant="outline" onClick={() => setConfirmPrune(false)} disabled={importing}>
                Cancel
              </Button>
              <Button
                onClick={() => void run()}
                disabled={importing}
                className="bg-red-600 hover:bg-red-700 text-white"
              >
                {importing ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : null}
                Delete &amp; import
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default ImportWorkflowsModal;
