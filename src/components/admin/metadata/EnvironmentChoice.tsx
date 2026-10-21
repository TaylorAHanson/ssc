import { EnvBadge } from './badges';
import { useMetadata } from './context';

export interface EnvironmentChoiceProps {
  value: string[];
  onChange: (environments: string[]) => void;
  /** Environments the changed objects are already in; not offered as copies. */
  exclude?: string[];
  disabled?: boolean;
}

/** "Also make these changes in": copies every change to the same object in the ticked environments. */
export function EnvironmentChoice({ value, onChange, exclude = [], disabled }: EnvironmentChoiceProps) {
  const { config } = useMetadata();
  const options = config.environments.filter((env) => !exclude.includes(env.key));
  if (config.environments.length < 2 || !options.length) return null;
  return (
    <fieldset className="space-y-1 rounded-md border border-gray-200 px-3 py-2" disabled={disabled}>
      <legend className="px-1 text-sm font-medium text-gray-900">Also make these changes in</legend>
      <p className="text-xs text-gray-500">
        Copies each change to the same object in every environment you tick, for example from <code>sales_dev</code> to <code>sales_prd</code>. Objects
        with no counterpart there are only changed where they are.
      </p>
      <div className="flex flex-wrap gap-4">
        {options.map((env) => (
          <label key={env.key} className="inline-flex cursor-pointer items-center gap-1.5 text-sm">
            <input
              type="checkbox"
              checked={value.includes(env.key)}
              onChange={(e) => onChange(e.target.checked ? [...value, env.key] : value.filter((k) => k !== env.key))}
              className="h-3.5 w-3.5 rounded border-gray-300 text-primary focus:ring-primary"
            />
            <EnvBadge env={env.key} />
            {env.approval === 'all' && <span className="text-xs text-gray-500">(every change needs approval)</span>}
          </label>
        ))}
      </div>
    </fieldset>
  );
}
