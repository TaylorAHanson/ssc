import { useState } from 'react';
import { Button } from '../../ui/button';
import { TagKeyInput } from './TagKeyInput';
import { TagValueInput } from './TagValueInput';
import { isReservedKey, RESERVED_KEY_HINT } from './tagModel';

// "Set tag [key] = [value] on all N shown": applies one tag to every row the
// current filter shows. With `onRename`, it can instead rename a key on them,
// keeping each value. Narrow the filter first to change fewer.
export function BulkTagBar({
  count,
  noun,
  onApply,
  onRemove,
  onRename,
}: {
  count: number;
  /** Plural noun for what's shown, e.g. "objects" or "columns". */
  noun: string;
  onApply: (key: string, value: string) => void;
  onRemove: (key: string) => void;
  onRename?: (from: string, to: string) => void;
}) {
  const [mode, setMode] = useState<'set' | 'rename'>('set');
  const [key, setKey] = useState('');
  const [value, setValue] = useState('');
  const [newKey, setNewKey] = useState('');
  const k = key.trim();
  const to = newKey.trim();
  const reserved = isReservedKey(k) || (mode === 'rename' && isReservedKey(to));
  const disabled = !k || count === 0 || reserved;
  const renameDisabled = disabled || !to || to === k;

  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-gray-700">
      {onRename ? (
        <select
          value={mode}
          onChange={(e) => setMode(e.target.value as 'set' | 'rename')}
          aria-label="Bulk action"
          className="h-7 rounded-md border border-gray-300 bg-white px-1.5 text-xs focus:ring-1 focus:ring-blue-500"
        >
          <option value="set">Set tag</option>
          <option value="rename">Rename key</option>
        </select>
      ) : (
        <span>Set tag</span>
      )}
      <TagKeyInput
        value={key}
        onChange={setKey}
        ariaLabel={mode === 'rename' ? `Tag key to rename on all shown ${noun}` : `Tag key to set on all shown ${noun}`}
        className={`h-7 w-36 rounded-md border px-2 font-mono focus:ring-1 focus:ring-blue-500 ${
          isReservedKey(k) ? 'border-rose-400 bg-rose-50' : 'border-gray-300 bg-white'
        }`}
      />
      {mode === 'rename' ? (
        <>
          <span className="text-gray-500">to</span>
          <TagKeyInput
            value={newKey}
            onChange={setNewKey}
            ariaLabel={`New tag key for all shown ${noun}`}
            className={`h-7 w-36 rounded-md border px-2 font-mono focus:ring-1 focus:ring-blue-500 ${
              isReservedKey(to) ? 'border-rose-400 bg-rose-50' : 'border-gray-300 bg-white'
            }`}
          />
          <span>
            on <strong>all {count}</strong> shown {noun}, keeping each value
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={renameDisabled}
            onClick={() => onRename?.(k, to)}
            className="h-7 text-xs bg-white"
          >
            Rename
          </Button>
        </>
      ) : (
        <>
          <span className="text-gray-400 font-mono">=</span>
          <span className="w-44">
            <TagValueInput
              tagKey={k}
              value={value}
              onChange={setValue}
              onEnter={() => !disabled && onApply(k, value)}
              ariaLabel={`Tag value to set on all shown ${noun}`}
              className="h-7 rounded-md border border-gray-300 bg-white px-2 font-mono focus:ring-1 focus:ring-blue-500"
            />
          </span>
          <span>
            on <strong>all {count}</strong> shown {noun}
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={disabled}
            onClick={() => onApply(k, value)}
            className="h-7 text-xs bg-white"
          >
            Apply
          </Button>
          <Button
            variant="ghost"
            size="sm"
            disabled={disabled}
            onClick={() => onRemove(k)}
            className="h-7 text-xs text-rose-700 hover:bg-rose-50"
            title={`Remove this tag from all ${count} shown ${noun}`}
          >
            Remove this tag
          </Button>
        </>
      )}
      {reserved && <span className="basis-full text-[11px] text-rose-700">{RESERVED_KEY_HINT}</span>}
    </div>
  );
}
