import { useId, useState, type ChangeEvent } from 'react';
import { previewImport, submitImport } from '../../../services/metadataApi';
import type { ImportRequest, ImportRow } from '../../../types/metadata';
import { Textarea } from '../../ui/textarea';
import { Notice } from '../../rbac/ui';
import { useMetadata } from './context';
import { ReviewDialog } from './ReviewDialog';
import { useReviewFlow } from './useReviewFlow';

const CSV_EXAMPLE = `name,column,comment,tags
my_catalog.bronze.orders,,Raw orders from the ERP,"{""domain"": ""sales"", ""pii"": null}"
my_catalog.bronze.orders,email,Customer email,"{""classification"": ""confidential""}"`;

const JSON_EXAMPLE = `[
  {"name": "my_catalog.bronze.orders", "comment": "Raw orders from the ERP", "tags": {"domain": "sales", "pii": null}},
  {"name": "my_catalog.bronze.orders", "column": "email", "tags": {"classification": "confidential"}}
]`;

type Parsed = { ok: true; request: Omit<ImportRequest, 'mode' | 'reason'>; rows: number } | { ok: false; error: string };

function parse(text: string): Parsed | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  if (trimmed.startsWith('[') || trimmed.startsWith('{')) {
    try {
      const data = JSON.parse(trimmed) as unknown;
      const rows = Array.isArray(data) ? data : (data as { rows?: unknown }).rows;
      if (!Array.isArray(rows)) return { ok: false, error: 'JSON must be a list of rows, or an object with a "rows" list.' };
      if (rows.some((r) => !r || typeof r !== 'object' || typeof (r as ImportRow).name !== 'string')) return { ok: false, error: 'Every row needs a "name".' };
      return { ok: true, request: { rows: rows as ImportRow[] }, rows: rows.length };
    } catch (e) {
      return { ok: false, error: `That isn’t valid JSON: ${e instanceof Error ? e.message : 'parse error'}` };
    }
  }
  const lines = trimmed.split(/\r?\n/).filter((l) => l.trim());
  if (!/^\s*name\s*,/i.test(lines[0] ?? '')) return { ok: false, error: 'CSV needs a header row starting with name, e.g. name,column,comment,tags.' };
  return { ok: true, request: { csv: trimmed }, rows: lines.length - 1 };
}

/** Bulk-declare tags and descriptions from a CSV or JSON file, previewed before anything is saved. */
export function ImportDialog({ onClose }: { onClose: () => void }) {
  const { bumpRefresh } = useMetadata();
  const [text, setText] = useState('');
  const [mode, setMode] = useState<'merge' | 'replace'>('merge');
  const [reason, setReason] = useState('');
  const [fileName, setFileName] = useState<string | null>(null);
  const id = useId();
  const parsed = parse(text);
  const flow = useReviewFlow<ImportRequest>({ preview: previewImport, submit: (r) => submitImport(r) });

  const request = (r = reason): ImportRequest | null => (parsed?.ok ? { ...parsed.request, mode, reason: r } : null);
  const optionsKey = (r: ImportRequest | null) => (r ? JSON.stringify([r.csv, r.rows, r.mode]) : '');
  const stale = flow.state.step === 'preview' && optionsKey(flow.state.request) !== optionsKey(request());

  const onFile = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setFileName(file.name);
    setText(await file.text());
  };

  const close = () => {
    if (flow.state.step === 'result' && flow.state.result.status !== 'rejected') bumpRefresh();
    onClose();
  };

  return (
    <ReviewDialog
      title="Import CSV or JSON"
      subtitle="Declare tags and descriptions for many objects at once. You’ll see exactly what changes before anything is saved."
      flow={flow}
      reason={reason}
      onReasonChange={setReason}
      buildRequest={(r) => request(r) as ImportRequest}
      onClose={close}
      onPreview={() => {
        const r = request();
        if (r) flow.start(r);
      }}
      previewDisabled={!parsed?.ok}
      stale={stale}
      reasonPlaceholder="e.g. Classification review for Q3"
    >
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <label htmlFor={`${id}-file`} className="text-sm font-medium text-gray-900">
            File
          </label>
          <input id={`${id}-file`} type="file" accept=".csv,.json,text/csv,application/json" onChange={onFile} className="text-sm" />
          {fileName && <span className="text-xs text-gray-500">{fileName}</span>}
        </div>
        <div>
          <label htmlFor={`${id}-text`} className="mb-1 block text-sm font-medium text-gray-900">
            Or paste it
          </label>
          <Textarea
            id={`${id}-text`}
            rows={6}
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              setFileName(null);
            }}
            placeholder={CSV_EXAMPLE}
            className="font-mono text-xs"
          />
          {parsed && !parsed.ok ? (
            <p className="mt-1 text-xs text-red-700" role="alert">
              {parsed.error}
            </p>
          ) : parsed ? (
            <p className="mt-1 text-xs text-gray-500">{parsed.rows.toLocaleString()} rows.</p>
          ) : null}
        </div>
        <fieldset className="space-y-1">
          <legend className="mb-1 text-sm font-medium text-gray-900">For objects that already have tags</legend>
          <label className="flex cursor-pointer items-start gap-2 text-sm">
            <input type="radio" name={`${id}-mode`} checked={mode === 'merge'} onChange={() => setMode('merge')} className="mt-0.5" />
            <span>
              Add and update <span className="block text-xs text-gray-500">Tags in the file are set; tags not in the file are left alone.</span>
            </span>
          </label>
          <label className="flex cursor-pointer items-start gap-2 text-sm">
            <input type="radio" name={`${id}-mode`} checked={mode === 'replace'} onChange={() => setMode('replace')} className="mt-0.5" />
            <span>
              Replace <span className="block text-xs text-gray-500">Each object ends up with exactly the tags in the file; others are removed.</span>
            </span>
          </label>
        </fieldset>
        <details className="text-xs text-gray-600">
          <summary className="cursor-pointer">File format</summary>
          <p className="mt-1">
            CSV needs a header row: <code>name,column,comment,tags</code>, where <code>tags</code> is a JSON object and <code>null</code> makes a key-only tag. An
            empty comment leaves the description as it is. In JSON, an empty string (<code>""</code>) clears it.
          </p>
          <pre className="mt-1 overflow-x-auto rounded bg-gray-100 p-2 font-mono">{CSV_EXAMPLE}</pre>
          <pre className="mt-1 overflow-x-auto rounded bg-gray-100 p-2 font-mono">{JSON_EXAMPLE}</pre>
        </details>
        {mode === 'replace' && <Notice kind="warn">Replace removes tags that aren’t in the file. Check the preview carefully.</Notice>}
      </div>
    </ReviewDialog>
  );
}
