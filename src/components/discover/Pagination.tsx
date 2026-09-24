import { useRef } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';

/** Page numbers to show: always first/last, a window around the current page, gaps as null. */
function pageWindow(page: number, pageCount: number): Array<number | null> {
  const pages = new Set([0, pageCount - 1, page - 1, page, page + 1]);
  const sorted = [...pages].filter((p) => p >= 0 && p < pageCount).sort((a, b) => a - b);
  const out: Array<number | null> = [];
  sorted.forEach((p, i) => {
    if (i > 0 && p - sorted[i - 1] > 1) out.push(null);
    out.push(p);
  });
  return out;
}

interface PaginationProps {
  page: number;
  pageCount: number;
  total: number;
  pageSize: number;
  onPageChange: (page: number) => void;
}

export function Pagination({ page, pageCount, total, pageSize, onPageChange }: PaginationProps) {
  const ref = useRef<HTMLElement | null>(null);
  if (pageCount <= 1) return null;

  const go = (p: number) => {
    onPageChange(p);
    // Bring the top of this list back into view so the new page starts at its first item.
    ref.current?.closest('section')?.scrollIntoView({ block: 'start', behavior: 'smooth' });
  };
  const first = page * pageSize + 1;
  const last = Math.min(total, (page + 1) * pageSize);
  const btn =
    'min-w-8 h-8 px-2 inline-flex items-center justify-center rounded-lg text-xs font-semibold transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-default';

  return (
    <nav ref={ref} aria-label="Pagination" className="flex flex-wrap items-center justify-between gap-3 pt-1">
      <span className="text-xs text-slate-500">
        {first}–{last} of {total}
      </span>
      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={() => go(page - 1)}
          disabled={page === 0}
          className={`${btn} text-slate-600 hover:bg-slate-100`}
          aria-label="Previous page"
        >
          <ChevronLeft className="w-4 h-4" />
        </button>
        {pageWindow(page, pageCount).map((p, i) =>
          p === null ? (
            <span key={`gap-${i}`} className="px-1 text-xs text-slate-400">
              …
            </span>
          ) : (
            <button
              key={p}
              type="button"
              onClick={() => go(p)}
              aria-current={p === page ? 'page' : undefined}
              className={`${btn} ${p === page ? 'bg-slate-900 text-white' : 'text-slate-600 hover:bg-slate-100'}`}
            >
              {p + 1}
            </button>
          ),
        )}
        <button
          type="button"
          onClick={() => go(page + 1)}
          disabled={page === pageCount - 1}
          className={`${btn} text-slate-600 hover:bg-slate-100`}
          aria-label="Next page"
        >
          <ChevronRight className="w-4 h-4" />
        </button>
      </div>
    </nav>
  );
}
