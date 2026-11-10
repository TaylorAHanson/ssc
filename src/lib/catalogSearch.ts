/**
 * Ranked keyword search for the Discover page.
 *
 * A query becomes one term group per meaningful word, holding the word's stem.
 * An item matches when ANY group hits (OR), and scores by where each word hit, weighted by field: a word in the
 * name counts most, then tags, domains and KPIs, then the description, then the
 * owner, catalog and schema. Matching every word adds a bonus, so "late
 * shipments" ranks a table about late shipments above one that only mentions
 * shipments.
 *
 * Mirrors backend/app/services/catalog_search.py (the assistant's catalog
 * search); keep the two in step.
 */

const STOPWORDS = new Set([
  'a', 'about', 'all', 'an', 'and', 'any', 'are', 'by', 'data', 'dataset', 'datasets', 'do', 'find',
  'for', 'from', 'get', 'have', 'i', 'in', 'info', 'information', 'is', 'list', 'me', 'my', 'of', 'on',
  'or', 'show', 'table', 'tables', 'that', 'the', 'this', 'to', 'we', 'what', 'where', 'which', 'with',
]);

const SUFFIXES = ['ies', 'ing', 'es', 'ed', 's', 'y'];
const MIN_STEM = 4;
/** A word hit inside another word ("customer" in "dimcustomer") counts this much of a word-start hit. */
const INSIDE_WORD = 0.5;
/** Shorter forms inside a word are mostly coincidence ("late" in "unrelated"). */
const INSIDE_WORD_MIN = 5;
const ALL_WORDS_BONUS = 5;

export const SEARCH_WEIGHTS = {
  name: 10,
  tags: 6,
  description: 4,
  owner: 2,
  location: 2,
} as const;

export type SearchFieldName = keyof typeof SEARCH_WEIGHTS;
export type SearchFields = Partial<Record<SearchFieldName, string | null | undefined | Array<string | null | undefined>>>;

function words(text: string): string[] {
  return text.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
}

export function stem(word: string): string {
  const w = word.toLowerCase();
  for (const suffix of SUFFIXES) {
    if (w.endsWith(suffix) && w.length - suffix.length >= MIN_STEM) return w.slice(0, -suffix.length);
  }
  return w;
}

function tokenize(query: string): string[] {
  return words(query).filter((w) => w.length >= 2 && !STOPWORDS.has(w));
}

function normalized(text: string): string {
  return ` ${words(text).join(' ')} `;
}

export interface CatalogQuery {
  /** One group of forms per query word; empty when the query was all stopwords. */
  groups: string[][];
  /** The raw query, lowercased, for queries that are all stopwords. */
  raw: string;
}

export function parseCatalogQuery(query: string): CatalogQuery {
  const roots = [...new Set(tokenize(query).map(stem))];
  return { groups: roots.map((root) => [root]), raw: query.trim().toLowerCase() };
}

function fieldText(value: SearchFields[SearchFieldName]): string {
  if (!value) return '';
  return Array.isArray(value) ? value.filter(Boolean).join(' ') : value;
}

/** How well `fields` match `query`; 0 means no match. */
export function scoreFields(query: CatalogQuery, fields: SearchFields): number {
  const entries = (Object.keys(SEARCH_WEIGHTS) as SearchFieldName[])
    .map((name) => ({ weight: SEARCH_WEIGHTS[name], text: fieldText(fields[name]) }))
    .filter((f) => f.text);
  if (query.groups.length === 0) {
    if (!query.raw) return 0;
    const hit = entries.find((f) => f.text.toLowerCase().includes(query.raw));
    return hit ? hit.weight : 0;
  }
  const hays = entries.map((f) => ({ weight: f.weight, hay: normalized(f.text) }));
  let score = 0;
  let matched = 0;
  for (const forms of query.groups) {
    let best = 0;
    for (const { weight, hay } of hays) {
      for (const form of forms) {
        const value = hay.includes(` ${form}`) ? weight : form.length >= INSIDE_WORD_MIN && hay.includes(form) ? weight * INSIDE_WORD : 0;
        if (value > best) best = value;
      }
    }
    if (best > 0) matched += 1;
    score += best;
  }
  if (matched === 0) return 0;
  return matched === query.groups.length && query.groups.length > 1 ? score + ALL_WORDS_BONUS : score;
}

/**
 * Items that match `query`, best first: by score, then certified ones, then
 * the original order. An empty query keeps every item in order.
 */
export function rankByQuery<T>(
  items: T[],
  query: CatalogQuery,
  fieldsOf: (item: T) => SearchFields,
  certified: (item: T) => boolean = () => false,
): T[] {
  if (query.groups.length === 0 && !query.raw) return items;
  return items
    .map((item, i) => ({ item, i, s: scoreFields(query, fieldsOf(item)) }))
    .filter((x) => x.s > 0)
    .sort((a, b) => b.s - a.s || Number(certified(b.item)) - Number(certified(a.item)) || a.i - b.i)
    .map((x) => x.item);
}
