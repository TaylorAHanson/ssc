import { useEffect, useState } from 'react';
import { useLocation } from 'react-router-dom';

/** Page each list was on, by history entry (`location.key`) and `resetKey`. */
const pagesByEntry = new Map<string, number>();

/**
 * Client-side paging for one list. The page resets to the first whenever
 * `resetKey` changes (e.g. a new subdomain, search term, or filter), except
 * that going back or forward to a history entry returns the list to the page
 * it was left on there. Lists shown together need distinct `resetKey`s.
 */
export function usePagination<T>(items: T[], pageSize: number, resetKey: string) {
  const entry = useLocation().key;
  const [state, setState] = useState(() => ({
    entry,
    resetKey,
    page: pagesByEntry.get(`${entry}|${resetKey}`) ?? 0,
  }));
  if (state.entry !== entry || state.resetKey !== resetKey) {
    // Only the entry changed (e.g. a panel opened over the list): keep the page.
    let page = state.page;
    if (state.resetKey !== resetKey) {
      page = state.entry === entry ? 0 : (pagesByEntry.get(`${entry}|${resetKey}`) ?? 0);
    }
    setState({ entry, resetKey, page });
  }
  useEffect(() => {
    pagesByEntry.set(`${state.entry}|${state.resetKey}`, state.page);
  }, [state]);

  const pageCount = Math.max(1, Math.ceil(items.length / pageSize));
  const current = Math.min(state.page, pageCount - 1);
  return {
    pageItems: items.slice(current * pageSize, (current + 1) * pageSize),
    page: current,
    pageCount,
    setPage: (page: number) => setState((s) => ({ ...s, page })),
    total: items.length,
    pageSize,
  };
}
