import { useState } from 'react';

/**
 * Client-side paging for one list. The page resets to the first whenever
 * `resetKey` changes (e.g. a new subdomain, search term, or filter).
 */
export function usePagination<T>(items: T[], pageSize: number, resetKey: string) {
  const [page, setPage] = useState(0);
  const [key, setKey] = useState(resetKey);
  if (key !== resetKey) {
    setKey(resetKey);
    setPage(0);
  }
  const pageCount = Math.max(1, Math.ceil(items.length / pageSize));
  const current = Math.min(page, pageCount - 1);
  return {
    pageItems: items.slice(current * pageSize, (current + 1) * pageSize),
    page: current,
    pageCount,
    setPage,
    total: items.length,
    pageSize,
  };
}
