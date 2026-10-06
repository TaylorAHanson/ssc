import { useState } from 'react';
import { ChevronDown, ChevronRight, Loader2, Replace } from 'lucide-react';
import { Button } from '../../ui/button';
import { TagKeyInput } from './TagKeyInput';
import { isReservedKey, RESERVED_KEY_HINT } from './tagModel';

// "Rename tag key [domains] to [domain] everywhere": finds every catalog,
// schema, table, view and column carrying the key, adds them to the list and
// stages the rename, keeping each value. Nothing is applied until the change
// is reviewed and run like any other edit.
export function RenameKeyPanel({
  busy,
  columnsIncluded,
  onRename,
}: {
  busy: boolean;
  /** Column tags can be edited in this mode, so column uses are renamed too. */
  columnsIncluded: boolean;
  onRename: (from: string, to: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const f = from.trim();
  const t = to.trim();
  const reserved = isReservedKey(f) || isReservedKey(t);
  const disabled = busy || !f || !t || f === t || reserved;

  return (
    <div className="rounded-lg border border-gray-200">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-3 py-2 text-xs font-medium text-gray-700 hover:bg-gray-50 rounded-lg"
        aria-expanded={open}
      >
        {open ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
        <Replace className="w-3.5 h-3.5 text-gray-500" />
        Rename a tag key everywhere
      </button>
      {open && (
        <div className="space-y-2 border-t border-gray-100 px-3 pb-3 pt-2.5 pl-9">
          <p className="text-[11px] text-gray-500 max-w-3xl">
            Finds every catalog, schema, table and view{columnsIncluded ? ' and column' : ''} tagged with the old key,
            adds them to your list and renames the key on them, keeping each value. Nothing changes in Unity Catalog
            until you review and run the checks.
            {!columnsIncluded && ' Column tags are left alone because they can only be edited in Local Execution Mode.'}
          </p>
          <form
            className="flex flex-wrap items-center gap-2 text-xs text-gray-700"
            onSubmit={(e) => {
              e.preventDefault();
              if (!disabled) onRename(f, t);
            }}
          >
            <span>Rename</span>
            <TagKeyInput
              value={from}
              onChange={setFrom}
              ariaLabel="Tag key to rename"
              className={`h-7 w-44 rounded-md border px-2 font-mono focus:ring-1 focus:ring-blue-500 ${
                isReservedKey(f) ? 'border-rose-400 bg-rose-50' : 'border-gray-300 bg-white'
              }`}
            />
            <span>to</span>
            <TagKeyInput
              value={to}
              onChange={setTo}
              ariaLabel="New tag key"
              className={`h-7 w-44 rounded-md border px-2 font-mono focus:ring-1 focus:ring-blue-500 ${
                isReservedKey(t) ? 'border-rose-400 bg-rose-50' : 'border-gray-300 bg-white'
              }`}
            />
            <Button type="submit" variant="outline" size="sm" disabled={disabled} className="h-7 text-xs bg-white">
              {busy && <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" />}
              Find & rename
            </Button>
          </form>
          {reserved && <p className="text-[11px] text-rose-700">{RESERVED_KEY_HINT}</p>}
        </div>
      )}
    </div>
  );
}
