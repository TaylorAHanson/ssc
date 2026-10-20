import { Link } from 'react-router-dom';
import { ExternalLink } from 'lucide-react';
import type { ChangeResult, VersionConflictItem } from '../../../types/metadata';
import { Notice } from '../../rbac/ui';
import { useMetadata } from './context';
import { objectLabel } from './format';

/** The outcome of a submit in plain language, with links to the change and the request. */
export function ResultView({ result, onLeave }: { result: ChangeResult; onLeave?: () => void }) {
  const { config, openChangeSet } = useMetadata();
  const cs = result.change_set;
  const requestLink = result.request_id && (
    <Link to={`/requests/${result.request_id}`} className="inline-flex items-center gap-1 font-medium underline">
      Open the approval request <ExternalLink className="h-3 w-3" aria-hidden="true" />
    </Link>
  );
  const changeLink = cs && (
    <button
      type="button"
      onClick={() => {
        onLeave?.();
        openChangeSet(cs.id);
      }}
      className="font-medium underline"
    >
      View the saved change
    </button>
  );
  const applyNote =
    config.reconcile_mode === 'enforce'
      ? 'Unity Catalog is updated in the background, usually within a minute.'
      : config.reconcile_mode === 'plan'
        ? 'The reconciler is in plan mode, so Unity Catalog won’t change until an admin switches it to enforce.'
        : 'The reconciler is off, so Unity Catalog won’t change until an admin turns it on.';

  return (
    <div className="space-y-3">
      {result.replayed && (
        <Notice kind="info">This change had already been submitted, so nothing was saved twice. Here’s the earlier result.</Notice>
      )}
      {result.status === 'committed' && (
        <Notice kind="success" title="Saved">
          <p>{result.message}</p>
          <p className="mt-1">{applyNote}</p>
          <p className="mt-1">{changeLink}</p>
        </Notice>
      )}
      {result.status === 'pending_approval' && (
        <Notice kind="info" title="Sent for approval">
          <p>{result.message}</p>
          <p className="mt-1">{requestLink}</p>
        </Notice>
      )}
      {result.status === 'partial' && (
        <Notice kind="success" title="Partly saved, the rest sent for approval">
          <p>{result.message}</p>
          <p className="mt-1">{applyNote}</p>
          <p className="mt-1 flex flex-wrap gap-3">
            {changeLink}
            {requestLink}
          </p>
        </Notice>
      )}
      {result.status === 'noop' && (
        <Notice kind="info" title="Nothing to change">
          {result.message || 'Everything already had the values you chose.'}
        </Notice>
      )}
      {result.status === 'rejected' && (
        <Notice kind="warn" title="Nothing was saved">
          {result.message}
        </Notice>
      )}
    </div>
  );
}

/** A 409: values changed after they were loaded. */
export function ConflictPanel({ message, conflicts }: { message: string; conflicts: VersionConflictItem[] }) {
  return (
    <div className="space-y-3">
      <Notice kind="warn" title="Someone else changed these first">
        <p>{message}</p>
        <p className="mt-1">
          Reload to start from their values. Your own edits are kept, and you’ll review the change again before anything is saved.
        </p>
      </Notice>
      <div className="overflow-x-auto rounded-md border border-gray-200">
        <table className="w-full text-left text-sm">
          <thead className="bg-gray-50 text-xs text-gray-600">
            <tr>
              <th scope="col" className="px-3 py-2 font-medium">Object</th>
              <th scope="col" className="px-3 py-2 font-medium">What changed</th>
              <th scope="col" className="px-3 py-2 font-medium">Value now</th>
            </tr>
          </thead>
          <tbody>
            {conflicts.map((c, i) => (
              <tr key={`${c.fqn_key}-${c.key ?? 'comment'}-${i}`} className="border-t border-gray-100 align-top">
                <td className="px-3 py-2 font-mono text-xs">{objectLabel(c.name)}</td>
                <td className="px-3 py-2 text-xs">
                  {c.key ? <span className="font-mono">{c.key}</span> : 'Description'}
                  <span className="block text-gray-500">
                    {c.current_version === 0 ? 'was removed' : c.expected_version === 0 ? 'was added by someone else' : 'was changed'}
                  </span>
                </td>
                <td className="px-3 py-2 font-mono text-xs">
                  {c.current_version === 0 ? <span className="text-gray-400">not set</span> : c.current_value ?? <span className="text-gray-500">(key only)</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
