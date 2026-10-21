import { useId, useState, type FormEvent } from 'react';
import { Plus } from 'lucide-react';
import { Button } from '../../ui/button';
import { Input } from '../../ui/input';
import { useMetadata } from './context';
import { tagProblem } from './format';

/** Key + value inputs with suggestions; a blank value makes a key-only tag. */
export function AddTagForm({ onAdd, submitLabel = 'Add', compact }: {
  onAdd: (key: string, value: string | null) => void;
  submitLabel?: string;
  compact?: boolean;
}) {
  const { config, keys } = useMetadata();
  const [key, setKey] = useState('');
  const [value, setValue] = useState('');
  const [error, setError] = useState<string | null>(null);
  const id = useId();
  const k = key.trim();
  const policy = config.key_policies.find((p) => p.key === k);
  const info = keys.find((i) => i.key === k);
  const valueOptions = policy?.allowed_values.length ? policy.allowed_values : info?.values.map((v) => v.value) ?? [];

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const v = value.trim() || null;
    const problem = tagProblem(k, v, config, keys);
    if (problem) {
      setError(problem);
      return;
    }
    onAdd(k, v);
    setKey('');
    setValue('');
    setError(null);
  };

  return (
    <form onSubmit={submit} className="space-y-1" aria-label="Add a tag">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          value={key}
          onChange={(e) => {
            setKey(e.target.value);
            setError(null);
          }}
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
        <span className="text-gray-400" aria-hidden="true">=</span>
        <Input
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            setError(null);
          }}
          list={`${id}-values`}
          placeholder={policy?.allowed_values.length ? 'Choose a value' : 'Value (blank = key only)'}
          aria-label="Tag value"
          className={compact ? 'h-8 w-48 text-xs' : 'h-9 w-52'}
        />
        <datalist id={`${id}-values`}>
          {valueOptions.map((v) => (
            <option key={v} value={v} />
          ))}
        </datalist>
        <Button type="submit" size="sm" variant="outline" className={compact ? 'h-8 gap-1 text-xs' : 'gap-1'}>
          <Plus className="h-3.5 w-3.5" /> {submitLabel}
        </Button>
      </div>
      {policy && (policy.sensitive || policy.description) && !error && (
        <p className="text-xs text-gray-500">
          {policy.description}
          {policy.sensitive && ' Changing this key needs approval unless you’re an owner allowed to set it.'}
        </p>
      )}
      {error && (
        <p className="text-xs text-red-700" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}
