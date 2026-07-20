import { useRef, useState } from 'react';
import { AlertTriangle, CheckCircle2, Loader2, Upload, X } from 'lucide-react';
import { Button } from '../ui/button';
import { api } from '../../services/api';
import type { ContextCatalogBundle, ContextImportReport } from '../../services/api';

interface Props {
  onImported: () => void;
  onClose: () => void;
}

/** Inbound half of env promotion for the Context Catalog: paste/upload a bundle
 *  exported from another environment and upsert it here. Domains are matched by
 *  slug and documents by title within their domain. */
export function ImportContextCatalogModal({ onImported, onClose }: Props) {
  const [text, setText] = useState('');
  const [docStatus, setDocStatus] = useState<'keep' | 'draft' | 'published'>('keep');
  const [overwrite, setOverwrite] = useState(true);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<ContextImportReport | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const onFile = async (file: File) => {
    setText(await file.text());
    setError(null);
  };

  const run = async () => {
    let bundle: ContextCatalogBundle;
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
      const r = await api.importContextCatalogBundle(bundle, { docStatus, overwrite });
      setReport(r);
      onImported();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Import failed');
    } finally {
      setImporting(false);
    }
  };

  const summary = (label: string, r: { created: string[]; updated: string[]; skipped: string[] }) =>
    `${label}: ${r.created.length} created · ${r.updated.length} updated · ${r.skipped.length} skipped`;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg max-h-[88vh] flex flex-col">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200">
          <div className="flex items-center gap-2">
            <Upload className="w-4 h-4 text-accent" />
            <h2 className="text-sm font-semibold">Import context catalog</h2>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-5 space-y-3 overflow-y-auto">
          <p className="text-xs text-gray-500">
            Paste a bundle exported from another environment, or load a <code>.json</code> file.
            Domains are matched by slug and documents by title within their domain; the domain tree
            re-links by slug.
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
            placeholder='{ "format": "selfservice.context_catalog/v1", "domains": [ ... ] }'
            value={text}
            onChange={(e) => setText(e.target.value)}
          />

          <div className="flex flex-wrap items-center gap-4">
            <label className="text-xs text-gray-600 flex items-center gap-1.5">
              Document status
              <select
                className="border border-gray-300 rounded-md h-8 px-2 text-xs"
                value={docStatus}
                onChange={(e) => setDocStatus(e.target.value as 'keep' | 'draft' | 'published')}
              >
                <option value="keep">keep as exported</option>
                <option value="draft">force draft</option>
                <option value="published">force published</option>
              </select>
            </label>
            <label className="text-xs text-gray-600 inline-flex items-center gap-1.5">
              <input type="checkbox" checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} />
              Overwrite existing (same slug / title)
            </label>
          </div>

          {error && (
            <div className="text-xs text-red-600 bg-red-50 border border-red-200 rounded-md px-3 py-2 flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" /> {error}
            </div>
          )}

          {report && (
            <div className="text-xs border border-gray-200 rounded-md divide-y divide-gray-100">
              <div className="px-3 py-1.5 flex items-center gap-2 text-green-700">
                <CheckCircle2 className="w-3.5 h-3.5" />
                {summary('Domains', report.domains)}
              </div>
              <div className="px-3 py-1.5 flex items-center gap-2 text-green-700">
                <CheckCircle2 className="w-3.5 h-3.5" />
                {summary('Documents', report.documents)}
              </div>
              {report.errors.length > 0 && (
                <div className="px-3 py-1.5 text-red-600 space-y-0.5">
                  {report.errors.map((e, i) => (
                    <div key={i}>
                      {e.domain ? `${e.domain}: ` : ''}
                      {e.error}
                    </div>
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
          <Button onClick={run} disabled={importing || !text.trim()}>
            {importing ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Upload className="w-4 h-4 mr-1" />}
            Import
          </Button>
        </div>
      </div>
    </div>
  );
}

export default ImportContextCatalogModal;
