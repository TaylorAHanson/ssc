import { FileText } from 'lucide-react';
import { renderMarkdownSafe } from '../lib/markdown';
import type { StepReport } from '../types';

/** The report's first line as plain text (e.g. "Compliant · 92% confidence"), for the collapsed row. */
function headline(markdown: string): string {
  const first = markdown.split('\n').find((line) => line.trim()) ?? '';
  return first.replace(/^#+\s*/, '').replace(/[*_`]/g, '').trim();
}

/**
 * Reports earlier workflow steps produced for the approver (any step whose
 * tool returned `report_markdown`), shown collapsed with their headline.
 * Report text can quote untrusted content (e.g. a reviewed repository), so it
 * always goes through the sanitizing renderer.
 */
export function StepReportPanel({ reports }: { reports: StepReport[] }) {
  if (reports.length === 0) return null;
  return (
    <div className="mt-3 space-y-2">
      <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide flex items-center gap-1">
        <FileText className="w-3.5 h-3.5" />
        Reports
      </p>
      {reports.map((report) => (
        <details
          key={report.step}
          className="group rounded-lg border border-gray-200 bg-white"
        >
          <summary className="cursor-pointer select-none px-3 py-2 text-sm flex items-center gap-2">
            <span className="font-medium text-gray-900">{report.title}</span>
            <span className="text-gray-500 truncate">{headline(report.markdown)}</span>
          </summary>
          <div
            className="border-t border-gray-100 px-3 py-3 text-sm prose prose-sm agent-prose max-w-none overflow-x-auto"
            dangerouslySetInnerHTML={{ __html: renderMarkdownSafe(report.markdown) }}
          />
        </details>
      ))}
    </div>
  );
}
