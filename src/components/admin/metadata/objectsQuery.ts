import { create } from 'zustand';
import type { ObjectState, SearchQuery, SecurableType, SortField } from '../../../types/metadata';
import { splitSearch } from './format';

export interface ObjectsQuery {
  /** The search box: free text plus any `key=value` / `!key` tokens not yet turned into chips. */
  text: string;
  tags: string[];
  catalogs: string[];
  securable_types: SecurableType[];
  environments: string[];
  object_states: ObjectState[];
  editable_only: boolean;
  drift_only: boolean;
  include_columns: boolean;
  include_unmanaged: boolean;
  sort: SortField;
  order: 'asc' | 'desc';
  page: number;
  page_size: number;
}

export const DEFAULT_QUERY: ObjectsQuery = {
  text: '',
  tags: [],
  catalogs: [],
  securable_types: [],
  environments: [],
  object_states: [],
  editable_only: false,
  drift_only: false,
  include_columns: false,
  include_unmanaged: false,
  sort: 'name',
  order: 'asc',
  page: 1,
  page_size: 50,
};

interface ObjectsQueryStore extends ObjectsQuery {
  /** Any change other than paging goes back to page 1. */
  update: (patch: Partial<ObjectsQuery>) => void;
  reset: () => void;
}

// Kept outside the tab so filters survive switching tabs.
export const useObjectsQuery = create<ObjectsQueryStore>((set) => ({
  ...DEFAULT_QUERY,
  update: (patch) => set(() => ('page' in patch ? patch : { ...patch, page: 1 })),
  reset: () => set({ ...DEFAULT_QUERY }),
}));

export function toSearchQuery(q: ObjectsQuery, text: string): SearchQuery {
  const typed = splitSearch(text);
  return {
    q: typed.q || undefined,
    tags: [...q.tags, ...typed.tags],
    catalogs: q.catalogs,
    securable_types: q.securable_types,
    environments: q.environments,
    object_states: q.object_states,
    drift: q.drift_only ? true : null,
    include_columns: q.include_columns,
    editable_only: q.editable_only,
    include_unmanaged: q.include_unmanaged,
    sort: q.sort,
    order: q.order,
    page: q.page,
    page_size: q.page_size,
  };
}

export function activeFilterCount(q: ObjectsQuery): number {
  return (
    q.tags.length +
    q.catalogs.length +
    q.securable_types.length +
    q.environments.length +
    q.object_states.length +
    Number(q.editable_only) +
    Number(q.drift_only) +
    Number(q.include_columns) +
    Number(q.include_unmanaged) +
    Number(!!q.text.trim())
  );
}
