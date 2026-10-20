import { useBrandingStore } from '../../../stores/brandingStore';
import { CopyButton } from '../../rbac/ui';
import { useMetadata } from './context';

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || `${window.location.origin}/api/v1`}/metadata`;

/** How a service principal connects, and how to check it's recognised. */
export function ConnectHelp() {
  const brandName = useBrandingStore((s) => s.brandName);
  const { whoami } = useMetadata();
  const whoamiCmd = `curl -s -H "Authorization: Bearer $TOKEN" ${API_BASE}/whoami`;
  const changeCmd = `curl -s -X POST ${API_BASE}/changes \\
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \\
  -H "Idempotency-Key: $(uuidgen)" \\
  -d '{"reason": "Nightly load tagged its outputs", "ops": [{"op": "set_tags", "name": "my_catalog.my_schema.my_table", "tags": {"source_system": "erp"}}]}'`;

  return (
    <details className="rounded-lg border border-gray-200 bg-white">
      <summary className="cursor-pointer px-4 py-3 text-sm font-medium text-gray-900">How to connect a service principal</summary>
      <div className="space-y-3 border-t border-gray-100 px-4 py-3 text-sm text-gray-700">
        <ol className="list-decimal space-y-2 pl-5">
          <li>Add it here with its application ID and the object patterns it may change. Until then, its calls can only read.</li>
          <li>
            Get an OAuth token for it from your Databricks workspace and call {brandName ? `${brandName}’s` : 'the'} metadata API at{' '}
            <code className="break-all">{API_BASE}</code>.
          </li>
          <li>
            Check it’s recognised: <code>/whoami</code> should report <code>service_principal</code>. If it says <code>unregistered_service_principal</code>, the
            application ID here doesn’t match the one in its token. The “Last seen” column shows which identity headers arrived on its latest call.
          </li>
          <li>Send an Idempotency-Key with each change so retries never save twice. Changes outside its patterns are refused; sensitive ones go for approval unless it may skip approval.</li>
        </ol>
        <Snippet label="Check who you are" code={whoamiCmd} />
        <Snippet label="Make a change" code={changeCmd} />
        <p className="text-xs text-gray-500">
          You’re signed in as {whoami.actor.display_name ?? whoami.actor.id}; this page saw these identity headers on your request:{' '}
          {whoami.headers_seen.length ? whoami.headers_seen.join(', ') : 'none'}.
        </p>
      </div>
    </details>
  );
}

function Snippet({ label, code }: { label: string; code: string }) {
  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-xs font-medium text-gray-600">
        {label}
        <CopyButton value={code} label={`Copy: ${label}`} />
      </div>
      <pre className="overflow-x-auto rounded bg-gray-900 px-3 py-2 font-mono text-xs text-gray-100">{code}</pre>
    </div>
  );
}
