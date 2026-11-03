import { useId, useState, type FormEvent } from 'react';
import { Plus } from 'lucide-react';
import type { SecurableType } from '../../../types/metadata';
import { cn } from '../../../lib/utils';
import { Button } from '../../ui/button';
import { Input } from '../../ui/input';
import { useMetadata } from './context';
import { policyApplies, policyFor, settableKeys, tagProblem } from './format';

const SELECT_CLASS =
  'rounded-md border border-gray-300 bg-white px-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50';

/**
 * Key + value inputs with suggestions; a blank value makes a key-only tag. With "known keys only"
 * the key is picked from the policy keys that apply to every object of `securableTypes`.
 */
export function AddTagForm({ onAdd, submitLabel = 'Add', compact, securableTypes = [] }: {
  onAdd: (key: string, value: string | null) => void;
  submitLabel?: string;
  compact?: boolean;
  /** The kinds of object the tag goes on; narrows the key dropdown. Empty = any. */
  securableTypes?: SecurableType[];
}) {
  const { config, keys } = useMetadata();
  const [key, setKey] = useState('');
  const [value, setValue] = useState('');
  const [error, setError] = useState<string | null>(null);
  const id = useId();
  const k = key.trim();
  const knownOnly = config.key_mode === 'known_only';
  const choices = knownOnly ? settableKeys(config, securableTypes) : [];
  const policy = policyFor(config, k);
  const info = keys.find((i) => i.key === k);
  const valueOptions = policy?.allowed_values.length ? policy.allowed_values : info?.values.map((v) => v.value) ?? [];
  const valueRequired = !!policy?.value_required;
  const valueMissing = valueRequired && !value.trim();
  const requiredHere = !!policy?.required && (!securableTypes.length || securableTypes.some((t) => policyApplies(policy, t)));
  const notes = [
    policy?.description,
    policy?.sensitive && 'Changing this key needs approval unless you’re an owner allowed to set it.',
    requiredHere && 'This is a required tag: once added, it can’t be removed.',
  ].filter(Boolean);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const v = value.trim() || null;
    const problem = tagProblem(k, v, config);
    if (problem) {
      setError(problem);
      return;
    }
    onAdd(k, v);
    setKey('');
    setValue('');
    setError(null);
  };

  const changeKey = (next: string) => {
    setKey(next);
    setError(null);
  };

  return (
    <form onSubmit={submit} className="space-y-1" aria-label="Add a tag">
      <div className="flex flex-wrap items-center gap-2">
        {knownOnly ? (
          <select
            value={key}
            onChange={(e) => changeKey(e.target.value)}
            aria-label="Tag key"
            disabled={!choices.length}
            className={cn(SELECT_CLASS, compact ? 'h-8 w-40 text-xs' : 'h-9 w-44 text-sm', !key && 'text-gray-500')}
          >
            <option value="">{choices.length ? 'Choose a key' : 'No keys allowed here'}</option>
            {choices.map((p) => (
              <option key={p.key} value={p.key} title={p.description ?? undefined} className="text-gray-900">
                {p.key}
              </option>
            ))}
          </select>
        ) : (
          <>
            <Input
              value={key}
              onChange={(e) => changeKey(e.target.value)}
              list={`${id}-keys`}
              placeholder="Tag key"
              aria-label="Tag key"
              className={compact ? 'h-8 w-40 text-xs' : 'h-9 w-44'}
            />
            <datalist id={`${id}-keys`}>
              {keys.filter((i) => !i.exempt).map((i) => (
                <option key={i.key} value={i.key}>
                  {i.policy?.description ?? `${i.count} objects`}
                </option>
              ))}
            </datalist>
          </>
        )}
        <span className="text-gray-400" aria-hidden="true">=</span>
        <span className="inline-flex items-center gap-0.5">
          <Input
            value={value}
            onChange={(e) => {
              setValue(e.target.value);
              setError(null);
            }}
            list={`${id}-values`}
            placeholder={
              valueRequired
                ? policy?.allowed_values.length ? 'Choose a value (required)' : 'Value (required)'
                : policy?.allowed_values.length ? 'Choose a value' : 'Value (blank = key only)'
            }
            aria-label={valueRequired ? 'Tag value (required)' : 'Tag value'}
            aria-required={valueRequired || undefined}
            aria-describedby={valueMissing ? `${id}-value-required` : undefined}
            className={cn(compact ? 'h-8 w-48 text-xs' : 'h-9 w-52', valueMissing && 'border-red-300')}
          />
          {valueRequired && (
            <span className="text-red-600" aria-hidden="true" title="A value is required">
              *
            </span>
          )}
        </span>
        <datalist id={`${id}-values`}>
          {valueOptions.map((v) => (
            <option key={v} value={v} />
          ))}
        </datalist>
        <Button type="submit" size="sm" variant="outline" disabled={valueMissing} className={compact ? 'h-8 gap-1 text-xs' : 'gap-1'}>
          <Plus className="h-3.5 w-3.5" /> {submitLabel}
        </Button>
      </div>
      {knownOnly && !choices.length && !error && (
        <p className="text-xs text-gray-500">Only tag keys with a key policy may be added, and none applies here. Ask an admin to add one.</p>
      )}
      {valueMissing && !error && (
        <p id={`${id}-value-required`} className="text-xs text-red-700">
          A value is required for {k}. Enter one to add the tag.
        </p>
      )}
      {notes.length > 0 && !error && <p className="text-xs text-gray-500">{notes.join(' ')}</p>}
      {error && (
        <p className="text-xs text-red-700" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}
