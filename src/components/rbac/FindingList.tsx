import type { ReactNode } from 'react';
import { AlertTriangle, Ban } from 'lucide-react';
import type { RbacFinding, RbacSheetInfo } from '../../types/rbac';
import { fieldLabel, plural } from './utils';

interface FindingListProps {
  findings: RbacFinding[];
  /** Where a finding applies, e.g. the staged row it came from. */
  context?: (finding: RbacFinding) => ReactNode;
  sheet?: RbacSheetInfo | null;
  /** When the list is a capped sample (imports), the full count. */
  total?: number;
  denyHint?: string;
}

/** Policy findings, blocking (deny) first, then warnings. */
export function FindingList({ findings, context, sheet, total, denyHint = 'These block saving.' }: FindingListProps) {
  if (findings.length === 0) return null;
  const deny = findings.filter((f) => f.severity === 'deny');
  const warn = findings.filter((f) => f.severity !== 'deny');
  return (
    <div className="space-y-3">
      {deny.length > 0 && (
        <FindingGroup
          tone="deny"
          title={`${plural(deny.length, 'blocking finding')}`}
          hint={denyHint}
          findings={deny}
          context={context}
          sheet={sheet}
        />
      )}
      {warn.length > 0 && (
        <FindingGroup
          tone="warn"
          title={plural(warn.length, 'warning')}
          hint="Worth a look; they don't stop the change."
          findings={warn}
          context={context}
          sheet={sheet}
        />
      )}
      {total !== undefined && total > findings.length && (
        <p className="text-xs text-gray-500">Showing the first {findings.length} of {total} findings.</p>
      )}
    </div>
  );
}

function FindingGroup({ tone, title, hint, findings, context, sheet }: {
  tone: 'deny' | 'warn';
  title: string;
  hint: string;
  findings: RbacFinding[];
  context?: (finding: RbacFinding) => ReactNode;
  sheet?: RbacSheetInfo | null;
}) {
  const deny = tone === 'deny';
  const Icon = deny ? Ban : AlertTriangle;
  return (
    <section className={`rounded-md border ${deny ? 'border-red-200 bg-red-50' : 'border-amber-200 bg-amber-50'}`}>
      <header className={`flex items-center gap-2 px-3 py-2 text-sm font-medium ${deny ? 'text-red-800' : 'text-amber-900'}`}>
        <Icon className="h-4 w-4" /> {title}
        <span className="font-normal opacity-80">· {hint}</span>
      </header>
      <ul className={`max-h-80 divide-y overflow-y-auto border-t bg-white text-sm ${deny ? 'divide-red-100 border-red-200' : 'divide-amber-100 border-amber-200'}`}>
        {findings.map((f, i) => (
          <li key={i} className="px-3 py-2">
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
              {context && <span className="text-xs font-medium text-gray-600">{context(f)}</span>}
              <span className="text-gray-900">{f.message}</span>
            </div>
            <div className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-gray-500">
              {f.field && <span>{fieldLabel(f.field, sheet)}</span>}
              {f.principal && <code className="rounded bg-gray-100 px-1">{f.principal}</code>}
              {f.privilege && <code className="rounded bg-gray-100 px-1">{f.privilege}</code>}
              <span className="font-mono text-[11px] text-gray-400">{f.code}</span>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
