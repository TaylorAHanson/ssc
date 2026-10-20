import { useId, type KeyboardEvent } from 'react';
import { Search, X } from 'lucide-react';
import { describeTagFilter, parseTagFilter, splitSearch } from './format';
import { useObjectsQuery } from './objectsQuery';

/** Free-text search plus tag filter chips: `key=value`, `key=*` (has key) or `!key` (lacks key). */
export function SearchBar() {
  const text = useObjectsQuery((s) => s.text);
  const tags = useObjectsQuery((s) => s.tags);
  const update = useObjectsQuery((s) => s.update);
  const helpId = useId();

  const commitTokens = () => {
    const { q, tags: typed } = splitSearch(text);
    if (typed.length) update({ text: q, tags: [...new Set([...tags, ...typed])] });
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      commitTokens();
    } else if (e.key === 'Backspace' && !text && tags.length) {
      update({ tags: tags.slice(0, -1) });
    }
  };

  return (
    <div className="space-y-1">
      <div className="flex min-h-10 flex-wrap items-center gap-1.5 rounded-md border border-gray-300 bg-white px-2 py-1.5 focus-within:ring-2 focus-within:ring-primary focus-within:ring-offset-2">
        <Search className="h-4 w-4 shrink-0 text-gray-400" aria-hidden="true" />
        <ul className="contents" aria-label="Tag filters">
          {tags.map((raw) => {
            const chip = parseTagFilter(raw);
            return (
              <li
                key={raw}
                className={
                  chip?.op === 'missing'
                    ? 'inline-flex items-center gap-1 rounded-full border border-red-200 bg-red-50 px-2 py-0.5 font-mono text-xs text-red-800'
                    : 'inline-flex items-center gap-1 rounded-full border border-indigo-200 bg-indigo-50 px-2 py-0.5 font-mono text-xs text-indigo-800'
                }
                title={chip ? describeTagFilter(chip) : raw}
              >
                {raw}
                <button
                  type="button"
                  onClick={() => update({ tags: tags.filter((t) => t !== raw) })}
                  aria-label={`Remove filter ${chip ? describeTagFilter(chip) : raw}`}
                  className="rounded-full hover:bg-black/10"
                >
                  <X className="h-3 w-3" />
                </button>
              </li>
            );
          })}
        </ul>
        <input
          value={text}
          onChange={(e) => update({ text: e.target.value })}
          onKeyDown={onKeyDown}
          onBlur={commitTokens}
          placeholder={tags.length ? 'Add words or filters…' : 'Search names and descriptions, or filter: classification=restricted  pii=*  !data_owner'}
          aria-label="Search objects"
          aria-describedby={helpId}
          className="min-w-[12rem] flex-1 border-0 bg-transparent p-0.5 text-sm outline-none placeholder:text-gray-400"
        />
        {(text || tags.length > 0) && (
          <button
            type="button"
            onClick={() => update({ text: '', tags: [] })}
            className="shrink-0 text-gray-400 hover:text-gray-700"
            aria-label="Clear search"
          >
            <X className="h-4 w-4" />
          </button>
        )}
      </div>
      <p id={helpId} className="text-xs text-gray-500">
        Words match names and descriptions. Filter by tag with <code>key=value</code>, <code>key=*</code> (has the tag) or{' '}
        <code>!key</code> (doesn’t have it); press Enter to turn a filter into a chip.
      </p>
    </div>
  );
}
