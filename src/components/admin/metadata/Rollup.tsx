import type { ReactNode } from 'react';
import { ChevronRight } from 'lucide-react';

/** A titled section that starts collapsed, for detail most people don't need. */
export function Rollup({ title, hint, children, defaultOpen, id }: {
  title: ReactNode;
  hint?: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
  id?: string;
}) {
  return (
    <details id={id} open={defaultOpen} className="group rounded-lg border border-gray-200 bg-white">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 text-sm font-medium text-gray-900 [&::-webkit-details-marker]:hidden">
        <ChevronRight className="h-4 w-4 text-gray-400 transition-transform group-open:rotate-90" aria-hidden="true" />
        {title}
        {hint && <span className="ml-1 text-xs font-normal text-gray-500">{hint}</span>}
      </summary>
      <div className="space-y-4 border-t border-gray-100 px-4 py-4">{children}</div>
    </details>
  );
}
