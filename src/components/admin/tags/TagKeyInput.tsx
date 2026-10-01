import { useContext, useEffect, useId, useState } from 'react';
import { api } from '../../../services/api';
import type { GovernedTagInfo } from '../../../services/api';
import { SuggestedKeysContext } from './tagModel';

// Matches per typed text, kept for the page's lifetime.
const cache = new Map<string, Promise<GovernedTagInfo[]>>();

function search(q: string): Promise<GovernedTagInfo[]> {
  let hit = cache.get(q);
  if (!hit) {
    hit = api.searchGovernedTagKeys(q).catch(() => {
      cache.delete(q);
      return [];
    });
    cache.set(q, hit);
  }
  return hit;
}

// A tag key field whose suggestions combine the built-in governance keys with
// Unity Catalog governed tags matching what you've typed (there can be
// thousands, so they're searched rather than listed up front).
export function TagKeyInput({
  value,
  onChange,
  disabled,
  className,
  ariaLabel,
}: {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  className: string;
  ariaLabel: string;
}) {
  const listId = useId();
  const builtIn = useContext(SuggestedKeysContext);
  const q = value.trim().toLowerCase();
  const [found, setFound] = useState<{ q: string; tags: GovernedTagInfo[] } | null>(null);

  useEffect(() => {
    if (q.length < 2) return;
    let live = true;
    const timer = setTimeout(() => {
      search(q).then((tags) => live && setFound({ q, tags }));
    }, 250);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [q]);

  const governed = found && found.q === q ? found.tags : [];
  const governedKeys = new Set(governed.map((g) => g.key));
  const options = [
    ...builtIn.filter((k) => !governedKeys.has(k)).map((k) => ({ key: k, label: undefined as string | undefined })),
    ...governed.map((g) => ({
      key: g.key,
      label: g.allowed_values.length
        ? `governed · ${g.allowed_values.length} allowed value${g.allowed_values.length === 1 ? '' : 's'}`
        : 'governed',
    })),
  ];

  return (
    <>
      <input
        list={listId}
        placeholder="key"
        aria-label={ariaLabel}
        disabled={disabled}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={className}
      />
      <datalist id={listId}>
        {options.map((o) => (
          <option key={o.key} value={o.key} label={o.label} />
        ))}
      </datalist>
    </>
  );
}
