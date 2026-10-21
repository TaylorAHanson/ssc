import { create } from 'zustand';

export type SearchWhat = 'tables' | 'columns' | 'everything';

interface BrowseFilters {
  what: SearchWhat;
  outOfSync: boolean;
  editableOnly: boolean;
  tagKey: string;
  environment: string;
  set: (patch: Partial<Omit<BrowseFilters, 'set' | 'reset'>>) => void;
  reset: () => void;
}

const DEFAULTS = { what: 'tables' as SearchWhat, outOfSync: false, editableOnly: false, tagKey: '', environment: '' };

/** Search filters, kept while the person clicks between catalogs and schemas. */
export const useBrowseFilters = create<BrowseFilters>((set) => ({
  ...DEFAULTS,
  set: (patch) => set(patch),
  reset: () => set(DEFAULTS),
}));

export function activeFilterCount(f: Pick<BrowseFilters, keyof typeof DEFAULTS>): number {
  return Number(f.what !== 'tables') + Number(f.outOfSync) + Number(f.editableOnly) + Number(!!f.tagKey) + Number(!!f.environment);
}
