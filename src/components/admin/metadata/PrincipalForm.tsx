import { useId, useState, type FormEvent, type ReactNode } from 'react';
import { createPrincipal, updatePrincipal } from '../../../services/metadataApi';
import type { ChangeOpType, PrincipalIn, PrincipalOut } from '../../../types/metadata';
import { Button } from '../../ui/button';
import { Input } from '../../ui/input';
import { Switch } from '../../ui/switch';
import { Textarea } from '../../ui/textarea';
import { ErrorBanner, Modal, Notice } from '../../rbac/ui';
import { useMetadata } from './context';
import { OPERATION_LABELS, errorMessage } from './format';

const lines = (text: string) => [...new Set(text.split(/[\n,]/).map((s) => s.trim()).filter(Boolean))];

/** Register or edit a service principal and what it may change. */
export function PrincipalForm({ principal, onClose, onSaved }: {
  principal: PrincipalOut | null;
  onClose: () => void;
  onSaved: (p: PrincipalOut) => void;
}) {
  const { config } = useMetadata();
  const editing = !!principal;
  const [appId, setAppId] = useState(principal?.application_id ?? '');
  const [displayName, setDisplayName] = useState(principal?.display_name ?? '');
  const [description, setDescription] = useState(principal?.description ?? '');
  const [enabled, setEnabled] = useState(principal?.enabled ?? true);
  const [scopes, setScopes] = useState((principal?.scopes ?? []).join('\n'));
  const [keys, setKeys] = useState((principal?.allowed_keys ?? []).join('\n'));
  const [ops, setOps] = useState<ChangeOpType[]>(principal?.allowed_operations ?? []);
  const [envs, setEnvs] = useState<string[]>(principal?.environments ?? []);
  const [maxObjects, setMaxObjects] = useState(principal?.max_objects_per_change ? String(principal.max_objects_per_change) : '');
  const [skipApproval, setSkipApproval] = useState(principal?.can_skip_approval ?? false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const id = useId();

  const max = maxObjects.trim() ? Number(maxObjects) : null;
  const problem = !appId.trim()
    ? 'Enter the application ID.'
    : !displayName.trim()
      ? 'Enter a display name.'
      : max !== null && (!Number.isInteger(max) || max < 1)
        ? 'The object limit must be a whole number above zero.'
        : null;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (problem) {
      setError(problem);
      return;
    }
    const body: PrincipalIn = {
      application_id: appId.trim(),
      display_name: displayName.trim(),
      description: description.trim() || null,
      enabled,
      scopes: lines(scopes),
      allowed_keys: lines(keys),
      allowed_operations: ops,
      environments: envs,
      max_objects_per_change: max,
      can_skip_approval: skipApproval,
    };
    setBusy(true);
    setError(null);
    try {
      const saved = editing ? await updatePrincipal(body.application_id, body) : await createPrincipal(body);
      onSaved(saved);
    } catch (err) {
      setError(errorMessage(err, 'Couldn’t save the service principal'));
      setBusy(false);
    }
  };

  const toggle = <T,>(list: T[], value: T, on: boolean) => (on ? [...list, value] : list.filter((v) => v !== value));

  return (
    <Modal
      title={editing ? `Edit ${principal.display_name}` : 'Add a service principal'}
      subtitle="Service principals call the metadata API directly; this decides what they may change."
      onClose={onClose}
      wide
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" form={`${id}-form`} disabled={busy}>
            {busy ? 'Saving…' : editing ? 'Save' : 'Add'}
          </Button>
        </>
      }
    >
      <form id={`${id}-form`} onSubmit={submit} className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field id={`${id}-app`} label="Application ID" hint="The service principal’s application (client) ID.">
            <Input
              id={`${id}-app`}
              data-autofocus
              value={appId}
              disabled={editing}
              onChange={(e) => setAppId(e.target.value)}
              className="font-mono"
              placeholder="00000000-0000-0000-0000-000000000000"
            />
          </Field>
          <Field id={`${id}-name`} label="Display name">
            <Input id={`${id}-name`} value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder="Sales ingestion pipeline" />
          </Field>
        </div>
        <Field id={`${id}-desc`} label="Description" optional>
          <Input id={`${id}-desc`} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What it is and who looks after it" />
        </Field>
        <div className="flex items-center gap-2">
          <Switch id={`${id}-enabled`} checked={enabled} onCheckedChange={setEnabled} />
          <label htmlFor={`${id}-enabled`} className="text-sm text-gray-800">
            Enabled <span className="text-gray-500">(when off, its calls are refused)</span>
          </label>
        </div>
        <Field
          id={`${id}-scopes`}
          label="Objects it may change"
          hint="One name pattern per line, e.g. sales_* or sales_dev.bronze or *.bronze.*. With none, it can’t change anything."
        >
          <Textarea id={`${id}-scopes`} rows={3} value={scopes} onChange={(e) => setScopes(e.target.value)} className="font-mono text-sm" />
        </Field>
        {lines(scopes).length === 0 && <Notice kind="warn">No object patterns yet, so this principal can only read.</Notice>}
        <Field id={`${id}-keys`} label="Tag keys it may set" optional hint="One key or pattern per line, e.g. source_system or pipeline.*. Leave empty for any key the key policies allow.">
          <Textarea id={`${id}-keys`} rows={2} value={keys} onChange={(e) => setKeys(e.target.value)} className="font-mono text-sm" />
        </Field>
        <fieldset>
          <legend className="mb-1 text-sm font-medium text-gray-800">
            Operations <span className="font-normal text-gray-500">(none ticked = all)</span>
          </legend>
          <div className="grid gap-1 sm:grid-cols-2">
            {(Object.keys(OPERATION_LABELS) as ChangeOpType[]).map((op) => (
              <label key={op} className="inline-flex cursor-pointer items-center gap-2 text-sm">
                <input type="checkbox" checked={ops.includes(op)} onChange={(e) => setOps(toggle(ops, op, e.target.checked))} className="h-3.5 w-3.5 rounded border-gray-300" />
                {OPERATION_LABELS[op]}
              </label>
            ))}
          </div>
        </fieldset>
        {config.environments.length > 0 && (
          <fieldset>
            <legend className="mb-1 text-sm font-medium text-gray-800">
              Environments <span className="font-normal text-gray-500">(none ticked = all)</span>
            </legend>
            <div className="flex flex-wrap gap-4">
              {config.environments.map((env) => (
                <label key={env.key} className="inline-flex cursor-pointer items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={envs.includes(env.key)}
                    onChange={(e) => setEnvs(toggle(envs, env.key, e.target.checked))}
                    className="h-3.5 w-3.5 rounded border-gray-300"
                  />
                  {env.key}
                </label>
              ))}
            </div>
          </fieldset>
        )}
        <Field id={`${id}-max`} label="Most objects in one change" optional hint={`Leave empty to use the platform limit (${config.max_objects_per_change}).`}>
          <Input id={`${id}-max`} type="number" min={1} value={maxObjects} onChange={(e) => setMaxObjects(e.target.value)} className="w-40" />
        </Field>
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <Switch id={`${id}-skip`} checked={skipApproval} onCheckedChange={setSkipApproval} />
            <label htmlFor={`${id}-skip`} className="text-sm text-gray-800">
              May skip approval
            </label>
          </div>
          {skipApproval && (
            <Notice kind="warn">
              Its changes to sensitive keys and approval-only environments are saved without anyone approving them. Only turn this on for trusted automation
              with narrow patterns above.
            </Notice>
          )}
        </div>
        {error && <ErrorBanner message={error} />}
      </form>
    </Modal>
  );
}

function Field({ id, label, hint, optional, children }: { id: string; label: string; hint?: string; optional?: boolean; children: ReactNode }) {
  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-sm font-medium text-gray-800">
        {label} {optional && <span className="font-normal text-gray-500">(optional)</span>}
      </label>
      {children}
      {hint && <p className="mt-1 text-xs text-gray-500">{hint}</p>}
    </div>
  );
}
