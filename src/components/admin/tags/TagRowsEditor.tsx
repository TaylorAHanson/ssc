import { Plus, Trash2 } from 'lucide-react';
import { Button } from '../../ui/button';
import type { TagRow } from './tagModel';
import { isReservedKey, RESERVED_KEY_HINT } from './tagModel';
import { TagKeyInput } from './TagKeyInput';
import { TagValueInput } from './TagValueInput';

// Key = value rows for one target. Keys suggest built-in and governed tags;
// values suggest a governed tag's allowed values.
export function TagRowsEditor({
  rows,
  onChange,
  disabled = false,
}: {
  rows: TagRow[];
  onChange: (rows: TagRow[]) => void;
  disabled?: boolean;
}) {
  const update = (idx: number, field: keyof TagRow, value: string) => {
    const next = [...rows];
    next[idx] = { ...next[idx], [field]: value };
    onChange(next);
  };

  return (
    <div className="space-y-1.5 max-w-3xl">
      {rows.map((row, idx) => (
        <div key={idx} className="flex items-start gap-2">
          <span className="flex-1 min-w-0">
            <TagKeyInput
              ariaLabel="Tag key"
              disabled={disabled}
              className={`w-full border rounded-md h-8 px-2.5 text-xs font-mono focus:ring-1 focus:ring-blue-500 disabled:bg-gray-50 ${
                isReservedKey(row.key) ? 'border-rose-400 bg-rose-50' : 'border-gray-300'
              }`}
              value={row.key}
              onChange={(v) => update(idx, 'key', v)}
            />
            {isReservedKey(row.key) && (
              <span className="block mt-0.5 text-[10px] text-rose-700">{RESERVED_KEY_HINT}</span>
            )}
          </span>
          <span className="text-gray-400 font-mono text-xs leading-8">=</span>
          <TagValueInput
            tagKey={row.key}
            ariaLabel="Tag value"
            disabled={disabled}
            className="border border-gray-300 rounded-md h-8 px-2.5 text-xs font-mono focus:ring-1 focus:ring-blue-500 disabled:bg-gray-50"
            value={row.value}
            onChange={(v) => update(idx, 'value', v)}
          />
          <Button
            variant="ghost"
            size="sm"
            disabled={disabled}
            onClick={() => onChange(rows.filter((_, i) => i !== idx))}
            title="Remove tag"
            className="h-8 w-8 p-0 hover:bg-rose-50 hover:text-rose-600"
          >
            <Trash2 className="w-3.5 h-3.5 text-gray-400" />
          </Button>
        </div>
      ))}
      {!disabled && (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => onChange([...rows, { key: '', value: '' }])}
          className="text-xs text-gray-600 hover:text-gray-900 h-7 px-2"
        >
          <Plus className="w-3 h-3 mr-1" /> Add tag
        </Button>
      )}
    </div>
  );
}
