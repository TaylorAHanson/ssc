import { useState } from 'react';
import { CheckCircle2, GitCompare, Loader2, XCircle } from 'lucide-react';
import { api } from '../../services/api';
import type { RbacCompareResult, RbacSheetColumn } from '../../types/rbac';
import { Button } from '../ui/button';
import { Card, CardContent } from '../ui/card';
import { Badge, ErrorBanner } from './ui';
import { useLoad } from './useLoad';
import { errorMessage, loadSheets, plural } from './utils';

const KIND_LABELS: Record<string, string> = {
  bom: 'Byte-order mark',
  line_endings: 'Line endings',
  trailing_newline: 'Final line break',
  encoding: 'Encoding',
  header: 'Headers',
  quoting: 'Quoting',
  rows: 'Rows',
};

/** Upload the CSV a person exported for a sheet and see whether today's export matches it byte for byte. */
export function CompareCard({ sheetNames }: { sheetNames: string[] }) {
  const sheetsState = useLoad(loadSheets);
  const sheets = sheetsState.data ?? [];
  const options = [...new Set([...sheets.map((s) => s.name), ...sheetNames])];
  const [picked, setPicked] = useState('');
  const sheet = picked || options[0] || '';
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<RbacCompareResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const columns = sheets.find((s) => s.name === (result?.sheet ?? sheet))?.columns ?? [];

  const compare = async () => {
    if (!file || !sheet) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await api.compareRbacExport(sheet, file));
    } catch (e) {
      setError(errorMessage(e, 'Compare failed'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardContent className="space-y-4 pt-5">
        <div>
          <h3 className="flex items-center gap-2 text-base font-semibold text-gray-900">
            <GitCompare className="h-4 w-4" /> Compare with a manual export
          </h3>
          <p className="mt-1 text-sm text-gray-500">
            Checks that the app’s export of a sheet is identical to the CSV a person exports from the Excel workbook,
            so the Terraform pipeline can switch over without noticing.
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label htmlFor="rbac-compare-sheet" className="mb-1 block text-xs font-medium text-gray-600">Sheet</label>
            <select
              id="rbac-compare-sheet"
              value={sheet}
              onChange={(e) => {
                setPicked(e.target.value);
                setResult(null);
              }}
              className="h-9 rounded-md border border-gray-300 bg-white px-2 font-mono text-sm"
            >
              {options.map((name) => <option key={name} value={name}>{name}</option>)}
            </select>
          </div>
          <div>
            <label htmlFor="rbac-compare-file" className="mb-1 block text-xs font-medium text-gray-600">Manual export (.csv)</label>
            <input
              id="rbac-compare-file"
              type="file"
              accept=".csv,text/csv"
              onChange={(e) => {
                setFile(e.target.files?.[0] ?? null);
                setResult(null);
              }}
              className="block text-sm text-gray-700 file:mr-3 file:rounded-md file:border file:border-gray-300 file:bg-white file:px-3 file:py-1.5 file:text-sm hover:file:bg-gray-50"
            />
          </div>
          <Button onClick={compare} disabled={!file || !sheet || busy} className="gap-2">
            {busy && <Loader2 className="h-4 w-4 animate-spin" />} Compare
          </Button>
        </div>
        {error && <ErrorBanner message={error} />}
        {result && <CompareResultView result={result} columns={columns} />}
      </CardContent>
    </Card>
  );
}

function CompareResultView({ result, columns }: { result: RbacCompareResult; columns: RbacSheetColumn[] }) {
  if (result.identical) {
    return (
      <div className="flex items-center gap-4 rounded-lg border border-green-200 bg-green-50 p-5 text-green-800" role="status">
        <CheckCircle2 className="h-10 w-10 shrink-0" />
        <div>
          <div className="text-2xl font-bold">Byte-identical</div>
          <div className="text-sm">
            <span className="font-mono">{result.sheet}</span> matches the manual export exactly.{' '}
            <code className="text-xs" title={result.ours_sha256}>sha256 {result.ours_sha256.slice(0, 16)}…</code>
          </div>
        </div>
      </div>
    );
  }
  return (
    <div className="space-y-3">
      <div className="flex items-start gap-3 rounded-lg border border-red-200 bg-red-50 p-4 text-red-800" role="status">
        <XCircle className="mt-0.5 h-6 w-6 shrink-0" />
        <div className="min-w-0">
          <div className="text-lg font-semibold">Not identical</div>
          <div className="text-xs">
            Export <code title={result.ours_sha256}>{result.ours_sha256.slice(0, 12)}…</code> · manual file{' '}
            <code title={result.theirs_sha256}>{result.theirs_sha256.slice(0, 12)}…</code>
          </div>
        </div>
      </div>
      {result.differences.length > 0 && (
        <ul className="divide-y divide-gray-100 rounded-md border border-gray-200 bg-white text-sm">
          {result.differences.map((d, i) => (
            <li key={i} className="flex items-start gap-3 px-3 py-2">
              <Badge tone="amber" className="shrink-0">{KIND_LABELS[d.kind] ?? d.kind}</Badge>
              <span className="text-gray-800">{d.detail}</span>
            </li>
          ))}
        </ul>
      )}
      <RowsTable title="Rows only in the export" rows={result.only_in_ours} columns={columns} />
      <RowsTable title="Rows only in the manual file" rows={result.only_in_theirs} columns={columns} />
    </div>
  );
}

function RowsTable({ title, rows, columns }: { title: string; rows: string[][]; columns: RbacSheetColumn[] }) {
  if (rows.length === 0) return null;
  const width = Math.max(...rows.map((r) => r.length));
  const headers = columns.length === width ? columns.map((c) => c.header) : Array.from({ length: width }, (_, i) => `Column ${i + 1}`);
  return (
    <div>
      <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500">{title} ({plural(rows.length, 'row')})</div>
      <div className="max-h-72 overflow-auto rounded border border-gray-200 bg-white">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-gray-50 text-left text-gray-500">
            <tr>{headers.map((h, i) => <th key={i} scope="col" className="whitespace-nowrap px-2 py-1.5 font-medium">{h}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map((row, r) => (
              <tr key={r} className="border-t border-gray-100 align-top">
                {headers.map((_, c) => <td key={c} className="px-2 py-1 text-gray-800">{row[c] ?? ''}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
