import { useEffect, useId, useState } from 'react';
import { api } from '../../../services/api';
import type { GovernedTagInfo } from '../../../services/api';

// Tag policies change rarely, so each key is looked up once per page load.
const cache = new Map<string, Promise<GovernedTagInfo | null>>();

function lookup(key: string): Promise<GovernedTagInfo | null> {
  let hit = cache.get(key);
  if (!hit) {
    hit = api
      .getGovernedTags([key])
      .then((rows) => rows.find((r) => r.key === key) ?? null)
      .catch(() => {
        cache.delete(key); // retry next time rather than caching a failure
        return null;
      });
    cache.set(key, hit);
  }
  return hit;
}

/** The governed-tag policy for `key` (null while unknown or not governed). */
function useGovernedTag(key: string): GovernedTagInfo | null {
  const k = key.trim();
  // Results are stored with the key they answer, so a stale one is ignored
  // without resetting state when the key changes.
  const [found, setFound] = useState<{ key: string; info: GovernedTagInfo | null } | null>(null);
  useEffect(() => {
    if (!k) return;
    let live = true;
    const timer = setTimeout(() => {
      lookup(k).then((info) => live && setFound({ key: k, info }));
    }, 300);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [k]);
  return found && found.key === k ? found.info : null;
}

// A tag value field that knows the key's Unity Catalog tag policy: it suggests
// the allowed values and flags one that would be rejected when applied.
export function TagValueInput({
  tagKey,
  value,
  onChange,
  onEnter,
  disabled,
  className,
  ariaLabel,
}: {
  tagKey: string;
  value: string;
  onChange: (value: string) => void;
  onEnter?: () => void;
  disabled?: boolean;
  className: string;
  ariaLabel: string;
}) {
  const listId = useId();
  const governed = useGovernedTag(tagKey);
  const allowed = governed?.allowed_values ?? [];
  const invalid = allowed.length > 0 && value !== '' && !allowed.includes(value);

  return (
    <span className="relative flex-1 min-w-0">
      <input
        list={allowed.length ? listId : undefined}
        placeholder={allowed.length ? `one of: ${allowed.slice(0, 4).join(', ')}${allowed.length > 4 ? '…' : ''}` : 'value'}
        aria-label={ariaLabel}
        aria-invalid={invalid}
        title={invalid ? `Not allowed by the '${tagKey}' tag policy. Allowed: ${allowed.join(', ')}` : undefined}
        disabled={disabled}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && onEnter?.()}
        className={`${className} w-full ${invalid ? 'border-rose-400 bg-rose-50 focus:ring-rose-500' : ''}`}
      />
      {allowed.length > 0 && (
        <datalist id={listId}>
          {allowed.map((v) => (
            <option key={v} value={v} />
          ))}
        </datalist>
      )}
      {invalid && (
        <span className="block mt-0.5 text-[10px] text-rose-700 truncate">
          Allowed for <code>{tagKey}</code>: {allowed.join(', ')}
        </span>
      )}
    </span>
  );
}
