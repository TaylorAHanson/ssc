import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { ChevronDown } from 'lucide-react';
import { cn } from '../../lib/utils';
import {
  applySuggestion,
  multiChoices,
  optionDomId,
  packSelection,
  selectChoices,
  suggest,
  type EditorSpec,
  type Normalize,
} from './cellOptions';
import { PickerList, type PickerItem } from './PickerList';
import { splitPacked } from './utils';

type Done = (value: string | null, refocus: boolean) => void;

interface EditorProps {
  initial: string;
  label: string;
  onDone: Done;
}

const FIELD = 'w-full rounded border border-primary bg-white px-1.5 text-[13px] focus:outline-none focus:ring-2 focus:ring-primary/30';

/** Inline editor: Enter saves, Escape cancels, leaving the field saves. */
export function CellEditor({ spec, ...props }: EditorProps & { spec: EditorSpec }) {
  if (spec.kind === 'select') return <SelectEditor {...props} options={spec.options} match={spec.match} />;
  if (spec.kind === 'multi') return <MultiSelectEditor {...props} spec={spec} />;
  return <TextEditor {...props} packed={spec.packed} suggestions={spec.suggestions} />;
}

function useFinish(onDone: Done): Done {
  const finished = useRef(false);
  return (value, refocus) => {
    if (finished.current) return;
    finished.current = true;
    onDone(value, refocus);
  };
}

function useFocusOnMount<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  useEffect(() => {
    ref.current?.focus();
  }, []);
  return ref;
}

/** The option index an arrow, Home or End key moves to; null for any other key. */
function step(key: string, index: number, count: number): number | null {
  if (count === 0) return null;
  if (key === 'ArrowDown') return Math.min(index + 1, count - 1);
  if (key === 'ArrowUp') return Math.max(index - 1, 0);
  if (key === 'Home') return 0;
  if (key === 'End') return count - 1;
  return null;
}

/** Typing letters jumps to the first option starting with them, as in a native select. */
function useTypeahead(values: string[]) {
  const buffer = useRef({ text: '', at: 0 });
  return (e: KeyboardEvent): number | null => {
    if (e.key.length !== 1 || e.key === ' ' || e.ctrlKey || e.metaKey || e.altKey) return null;
    const now = Date.now();
    const text = (now - buffer.current.at < 700 ? buffer.current.text : '') + e.key.toLowerCase();
    buffer.current = { text, at: now };
    const index = values.findIndex((v) => v.toLowerCase().startsWith(text));
    return index >= 0 ? index : null;
  };
}

function SelectEditor({ initial, label, options, match, onDone }: EditorProps & { options: string[]; match: Normalize }) {
  const trigger = useFocusOnMount<HTMLDivElement>();
  const listId = useId();
  const choices = useMemo(() => selectChoices(initial, options, match), [initial, options, match]);
  const [active, setActive] = useState(choices.selected);
  const finish = useFinish(onDone);
  const typeahead = useTypeahead(choices.values);

  // Picking the option a value already matches keeps the value's own spelling.
  const commit = (index: number, refocus: boolean) => {
    const value = choices.values[index];
    finish(value === undefined || match(value) === match(initial) ? initial : value, refocus);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const next = step(e.key, active, choices.values.length);
    if (next !== null) {
      e.preventDefault();
      setActive(next);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      commit(active, true);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      finish(null, true);
    } else if (e.key === ' ') {
      e.preventDefault();
    } else {
      const hit = typeahead(e);
      if (hit !== null) setActive(hit);
    }
  };

  const current = choices.values[active] ?? '';
  const currentOutside = choices.outside && active === 0;
  const items: PickerItem[] = choices.values.map((value, i) => ({
    value,
    selected: i === active,
    outside: choices.outside && i === 0,
    note: choices.outside && i === 0 ? 'Current value; not one of the options' : undefined,
  }));

  return (
    <div className="min-w-[10rem]">
      <div
        ref={trigger}
        tabIndex={0}
        role="combobox"
        aria-label={`Edit ${label}`}
        aria-haspopup="listbox"
        aria-expanded="true"
        aria-controls={listId}
        aria-activedescendant={active >= 0 ? optionDomId(listId, active) : undefined}
        onKeyDown={onKeyDown}
        onBlur={() => commit(active, false)}
        className={cn(FIELD, 'flex h-6 cursor-default items-center justify-between gap-1')}
      >
        <span className={cn('truncate', !current && 'text-gray-400', currentOutside && 'text-red-800')}>{current || 'Choose…'}</span>
        <ChevronDown aria-hidden="true" className="h-3.5 w-3.5 shrink-0 text-gray-400" />
      </div>
      <PickerList
        anchor={trigger}
        id={listId}
        label={label}
        items={items}
        active={active}
        variant="select"
        onPick={(i) => commit(i, true)}
        footer="Enter saves, Esc cancels."
      />
    </div>
  );
}

function MultiSelectEditor({ initial, label, spec, onDone }: EditorProps & { spec: Extract<EditorSpec, { kind: 'multi' }> }) {
  const { options, match } = spec;
  const trigger = useFocusOnMount<HTMLDivElement>();
  const listId = useId();
  const choices = useMemo(() => multiChoices(initial, options, match), [initial, options, match]);
  const [chosen, setChosen] = useState<ReadonlySet<string>>(() => new Set(splitPacked(initial).map(match)));
  const [active, setActive] = useState(choices.length ? 0 : -1);
  const finish = useFinish(onDone);
  const typeahead = useTypeahead(choices.map((c) => c.value));

  const value = packSelection(initial, chosen, options, match);
  const outsideKeys = new Set(choices.filter((c) => c.outside).map((c) => match(c.value)));

  // Values outside the options can be taken off but never put back on.
  const toggle = (index: number) => {
    const choice = choices[index];
    if (!choice) return;
    const key = match(choice.value);
    setActive(index);
    setChosen((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else if (!choice.outside) next.add(key);
      return next;
    });
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const next = step(e.key, active, choices.length);
    if (next !== null) {
      e.preventDefault();
      setActive(next);
    } else if (e.key === ' ') {
      e.preventDefault();
      toggle(active);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      finish(value, true);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      finish(null, true);
    } else {
      const hit = typeahead(e);
      if (hit !== null) setActive(hit);
    }
  };

  const items: PickerItem[] = choices.map((c) => {
    const selected = chosen.has(match(c.value));
    return {
      value: c.value,
      selected,
      outside: c.outside,
      disabled: c.outside && !selected,
      group: c.outside ? 'Not on the allow list' : spec.heading,
      note: c.outside ? (selected ? 'Can be removed, but not added back' : 'Removed; it can’t be added back') : undefined,
    };
  });
  const atoms = splitPacked(value);

  return (
    <div className="min-w-[18rem]">
      <div
        ref={trigger}
        tabIndex={0}
        role="combobox"
        aria-label={`Edit ${label}: ${atoms.join(', ') || 'none selected'}`}
        aria-haspopup="listbox"
        aria-expanded="true"
        aria-controls={listId}
        aria-activedescendant={active >= 0 ? optionDomId(listId, active) : undefined}
        onKeyDown={onKeyDown}
        onBlur={() => finish(value, false)}
        className={cn(FIELD, 'flex min-h-6 cursor-default flex-wrap items-center gap-1 py-0.5')}
      >
        {atoms.length ? (
          atoms.map((atom, i) => (
            <span
              key={`${atom}-${i}`}
              className={cn(
                'rounded border px-1 text-xs',
                outsideKeys.has(match(atom)) ? 'border-red-200 bg-red-50 text-red-800' : 'border-gray-200 bg-gray-50 text-gray-700',
              )}
            >
              {atom}
            </span>
          ))
        ) : (
          <span className="text-gray-400">None selected</span>
        )}
      </div>
      <PickerList
        anchor={trigger}
        id={listId}
        label={label}
        items={items}
        active={active}
        variant="multi"
        onPick={toggle}
        notice={options.length === 0 ? spec.empty : undefined}
        footer="Space ticks, Enter saves, Esc cancels."
      />
    </div>
  );
}

function TextEditor({ initial, label, packed, suggestions, onDone }: EditorProps & { packed: boolean; suggestions: string[] }) {
  const input = useRef<HTMLInputElement>(null);
  const listId = useId();
  const [draft, setDraft] = useState(initial);
  const [typed, setTyped] = useState(false);
  const [open, setOpen] = useState(!initial && suggestions.length > 0);
  const [active, setActive] = useState(-1);
  const finish = useFinish(onDone);

  const matches = useMemo(() => (open ? suggest(suggestions, draft, packed, typed) : null), [open, suggestions, draft, packed, typed]);
  const shown = matches?.shown ?? [];
  const listed = shown.length > 0;

  const accept = (index: number) => {
    const value = shown[index];
    if (value === undefined) return;
    setDraft(applySuggestion(draft, value, packed, typed));
    setTyped(false);
    setOpen(false);
    setActive(-1);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && suggestions.length > 0) {
      e.preventDefault();
      if (!listed) {
        setOpen(true);
        setActive(0);
      } else {
        setActive((i) => step(e.key, i, shown.length) ?? i);
      }
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (listed && active >= 0) accept(active);
      else finish(draft, true);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      finish(null, true);
    }
  };

  const combobox = suggestions.length > 0;
  return (
    <div className={packed ? 'min-w-[18rem]' : 'min-w-[10rem]'}>
      <input
        ref={input}
        autoFocus
        value={draft}
        onChange={(e) => {
          setDraft(e.target.value);
          setTyped(true);
          setOpen(true);
          setActive(-1);
        }}
        onKeyDown={onKeyDown}
        onBlur={() => finish(draft, false)}
        aria-label={`Edit ${label}`}
        role={combobox ? 'combobox' : undefined}
        aria-autocomplete={combobox ? 'list' : undefined}
        aria-expanded={combobox ? listed : undefined}
        aria-controls={listed ? listId : undefined}
        aria-activedescendant={listed && active >= 0 ? optionDomId(listId, active) : undefined}
        className={cn(FIELD, 'h-6')}
      />
      {listed && (
        <PickerList
          anchor={input}
          id={listId}
          label={`Suggestions for ${label}`}
          items={shown.map((value) => ({ value, selected: false, group: 'Used on this sheet' }))}
          active={active}
          variant="suggest"
          onPick={accept}
          footer={matches?.more ? `${matches.more.toLocaleString()} more; keep typing to narrow.` : 'New values are fine too.'}
        />
      )}
      {packed && <p className="mt-0.5 text-[11px] text-gray-500">Separate values with “;”. Enter saves, Esc cancels.</p>}
    </div>
  );
}
