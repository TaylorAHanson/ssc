import { useCallback, useState } from 'react';
import { Pencil, Plus, Trash2 } from 'lucide-react';
import { deletePrincipal, listPrincipals } from '../../../services/metadataApi';
import type { PrincipalOut } from '../../../types/metadata';
import { Button } from '../../ui/button';
import { Badge, CopyButton, EmptyState, ErrorBanner, Modal, Notice, Skeleton } from '../../rbac/ui';
import { ConnectHelp } from './ConnectHelp';
import { useMetadata } from './context';
import { OPERATION_LABELS, errorMessage, formatTime, relativeTime } from './format';
import { PrincipalForm } from './PrincipalForm';
import { useAsync } from './useAsync';

/** Service principals allowed to call the metadata API, and what each may change. */
export function PrincipalsTab() {
  const { refreshToken, notify } = useMetadata();
  const load = useCallback(() => listPrincipals(), []);
  const { data, error, loading, reload } = useAsync(load, { keepPrevious: true, token: refreshToken });
  const [editing, setEditing] = useState<PrincipalOut | 'new' | null>(null);
  const [deleting, setDeleting] = useState<PrincipalOut | null>(null);
  const [busy, setBusy] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const confirmDelete = async () => {
    if (!deleting) return;
    setBusy(true);
    setDeleteError(null);
    try {
      await deletePrincipal(deleting.application_id);
      notify(`Removed ${deleting.display_name}. Its calls are now refused.`);
      setDeleting(null);
      reload();
    } catch (e) {
      setDeleteError(errorMessage(e, 'Couldn’t remove it'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-2xl text-sm text-gray-600">
          Automation such as pipelines or agents can change metadata through the API once it’s registered here. Each one is limited to the objects, keys and
          environments you allow.
        </p>
        <Button size="sm" className="gap-1.5" onClick={() => setEditing('new')}>
          <Plus className="h-3.5 w-3.5" /> Add a service principal
        </Button>
      </div>
      <ConnectHelp />

      {error ? <ErrorBanner message={errorMessage(error, 'Couldn’t load service principals')} onRetry={reload} /> : null}
      {!data && loading ? (
        <Skeleton rows={3} />
      ) : data && data.length === 0 ? (
        <EmptyState title="No service principals yet">Add one to let automation change metadata within the limits you set.</EmptyState>
      ) : data ? (
        <ul className="space-y-3">
          {data.map((p) => (
            <PrincipalCard key={p.application_id} principal={p} onEdit={() => setEditing(p)} onDelete={() => setDeleting(p)} />
          ))}
        </ul>
      ) : null}

      {editing && (
        <PrincipalForm
          principal={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={(p) => {
            notify(editing === 'new' ? `Added ${p.display_name}.` : `Saved ${p.display_name}.`);
            setEditing(null);
            reload();
          }}
        />
      )}
      {deleting && (
        <Modal
          title={`Remove ${deleting.display_name}?`}
          onClose={() => setDeleting(null)}
          footer={
            <>
              <Button variant="outline" onClick={() => setDeleting(null)}>
                Cancel
              </Button>
              <Button onClick={confirmDelete} disabled={busy} className="bg-red-600 hover:bg-red-700">
                {busy ? 'Removing…' : 'Remove'}
              </Button>
            </>
          }
        >
          <p className="text-sm text-gray-700">
            Its calls will be refused straight away. Changes it already made stay in the history. To pause it instead, edit it and turn off Enabled.
          </p>
          {deleteError && <ErrorBanner message={deleteError} />}
        </Modal>
      )}
    </div>
  );
}

function PrincipalCard({ principal: p, onEdit, onDelete }: { principal: PrincipalOut; onEdit: () => void; onDelete: () => void }) {
  const headers = Object.entries(p.last_seen_headers ?? {});
  return (
    <li className="rounded-lg border border-gray-200 bg-white p-4">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-gray-900">{p.display_name}</span>
            {p.enabled ? <Badge tone="green">Enabled</Badge> : <Badge tone="gray">Disabled</Badge>}
            {p.can_skip_approval && <Badge tone="amber">May skip approval</Badge>}
          </div>
          <div className="mt-0.5 flex items-center gap-1 font-mono text-xs text-gray-500">
            {p.application_id}
            <CopyButton value={p.application_id} label="Copy application ID" />
          </div>
          {p.description && <p className="mt-1 text-sm text-gray-600">{p.description}</p>}
        </div>
        <div className="flex gap-1">
          <Button size="sm" variant="ghost" className="gap-1" onClick={onEdit} aria-label={`Edit ${p.display_name}`}>
            <Pencil className="h-3.5 w-3.5" /> Edit
          </Button>
          <Button size="sm" variant="ghost" className="gap-1 text-red-700" onClick={onDelete} aria-label={`Remove ${p.display_name}`}>
            <Trash2 className="h-3.5 w-3.5" /> Remove
          </Button>
        </div>
      </div>
      <dl className="mt-3 grid gap-x-6 gap-y-1.5 text-sm sm:grid-cols-[auto,1fr]">
        <dt className="text-gray-500">Objects</dt>
        <dd className="flex flex-wrap gap-1">
          {p.scopes.length ? p.scopes.map((s) => <code key={s} className="rounded bg-gray-100 px-1 text-xs">{s}</code>) : <span className="text-amber-800">None, so it can only read</span>}
        </dd>
        <dt className="text-gray-500">Tag keys</dt>
        <dd className="flex flex-wrap gap-1">
          {p.allowed_keys.length ? p.allowed_keys.map((k) => <code key={k} className="rounded bg-gray-100 px-1 text-xs">{k}</code>) : <span className="text-gray-600">Any key the policies allow</span>}
        </dd>
        <dt className="text-gray-500">Operations</dt>
        <dd className="text-gray-700">{p.allowed_operations.length ? p.allowed_operations.map((o) => OPERATION_LABELS[o]).join(', ') : 'All'}</dd>
        <dt className="text-gray-500">Environments</dt>
        <dd className="text-gray-700">{p.environments.length ? p.environments.join(', ') : 'All'}</dd>
        {p.max_objects_per_change && (
          <>
            <dt className="text-gray-500">Per change</dt>
            <dd className="text-gray-700">At most {p.max_objects_per_change.toLocaleString()} objects</dd>
          </>
        )}
        <dt className="text-gray-500">Last seen</dt>
        <dd className="text-gray-700">
          {p.last_seen_at ? <span title={formatTime(p.last_seen_at)}>{relativeTime(p.last_seen_at)}</span> : <span className="text-gray-500">Never called the API</span>}
        </dd>
      </dl>
      {headers.length > 0 && (
        <details className="mt-2">
          <summary className="cursor-pointer text-xs text-gray-600">Identity headers on its last call</summary>
          <table className="mt-1 w-full text-left text-xs">
            <tbody>
              {headers.map(([k, v]) => (
                <tr key={k} className="border-t border-gray-100">
                  <th scope="row" className="py-1 pr-3 font-mono font-normal text-gray-500">{k}</th>
                  <td className="break-all py-1 font-mono text-gray-800">{typeof v === 'string' ? v : JSON.stringify(v)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
      {!p.enabled && <Notice kind="info" className="mt-2">Disabled: its calls are refused until you turn it back on.</Notice>}
    </li>
  );
}
