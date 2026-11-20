import { useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle2, KeyRound, Loader2, ServerCog, User, UserCheck, XCircle } from 'lucide-react';
import { Button } from '../ui/button';
import { checkServicePrincipal, getServicePrincipalUsage } from '../../services/api';
import type { ServicePrincipalCheck, ServicePrincipalUsage as Usage, ServicePrincipalUsageRow } from '../../services/api';
import { errorMessage } from '../../lib/errors';

const KIND_STYLE: Record<ServicePrincipalUsageRow['kind'], { icon: typeof KeyRound; className: string }> = {
  named: { icon: KeyRound, className: 'bg-indigo-50 text-indigo-700 ring-indigo-200' },
  target: { icon: ServerCog, className: 'bg-amber-50 text-amber-800 ring-amber-200' },
  app: { icon: ServerCog, className: 'bg-gray-100 text-gray-700 ring-gray-200' },
  user: { icon: User, className: 'bg-emerald-50 text-emerald-700 ring-emerald-200' },
};

/**
 * Admin → Settings → Service Principals, under the list: which identity each part of the app runs as,
 * read from the saved settings, plus a Check button per listed service principal that signs in as it.
 *
 * Remount it (via `key`) after a save so it reflects the saved settings.
 */
export function ServicePrincipalUsage() {
  const [usage, setUsage] = useState<Usage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [checks, setChecks] = useState<Record<string, ServicePrincipalCheck | 'running'>>({});

  useEffect(() => {
    let cancelled = false;
    getServicePrincipalUsage()
      .then((u) => !cancelled && setUsage(u))
      .catch((e) => !cancelled && setError(errorMessage(e, 'Failed to load which service principal each part uses')));
    return () => {
      cancelled = true;
    };
  }, []);

  const runCheck = async (name: string) => {
    setChecks((c) => ({ ...c, [name]: 'running' }));
    try {
      const result = await checkServicePrincipal(name);
      setChecks((c) => ({ ...c, [name]: result }));
    } catch (e) {
      setChecks((c) => ({
        ...c,
        [name]: { name, ok: false, message: errorMessage(e, 'Check failed'), application_id: '', display_name: '' },
      }));
    }
  };

  if (error) return <p className="text-sm text-red-600">{error}</p>;
  if (!usage) {
    return (
      <p className="flex items-center gap-2 text-sm text-gray-500">
        <Loader2 className="w-4 h-4 animate-spin" /> Loading…
      </p>
    );
  }

  return (
    <div className="space-y-5">
      {usage.names.length > 0 && (
        <div>
          <p className="text-sm text-gray-600 mb-2">
            Check that the app can read a saved service principal's secrets and sign in as it on this workspace.
          </p>
          <ul className="space-y-1.5">
            {usage.names.map((name) => {
              const result = checks[name];
              return (
                <li key={name} className="flex flex-wrap items-center gap-3 text-sm">
                  <span className="font-medium text-gray-900 min-w-[10rem]">{name}</span>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="gap-1.5"
                    onClick={() => runCheck(name)}
                    disabled={result === 'running'}
                  >
                    {result === 'running' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <UserCheck className="w-3.5 h-3.5" />}
                    Check
                  </Button>
                  {result && result !== 'running' && (
                    <span className={`inline-flex items-start gap-1.5 ${result.ok ? 'text-emerald-700' : 'text-red-700'}`}>
                      {result.ok ? <CheckCircle2 className="w-4 h-4 mt-0.5 shrink-0" /> : <XCircle className="w-4 h-4 mt-0.5 shrink-0" />}
                      {result.message}
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}

      <div className="overflow-x-auto rounded-lg border border-gray-200">
        <table className="min-w-full text-sm">
          <thead className="bg-gray-50 text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
            <tr>
              <th className="px-3 py-2">What</th>
              <th className="px-3 py-2">Runs as</th>
              <th className="px-3 py-2">Change it in</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {usage.rows.map((row) => {
              const style = KIND_STYLE[row.kind];
              const Icon = style.icon;
              return (
                <tr key={row.feature} className="align-top">
                  <td className="px-3 py-2.5">
                    <p className="font-medium text-gray-900">{row.feature}</p>
                    <p className="mt-0.5 text-xs text-gray-500">{row.detail}</p>
                  </td>
                  <td className="px-3 py-2.5">
                    <span className={`inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-xs font-medium ring-1 ${style.className}`}>
                      <Icon className="w-3.5 h-3.5" />
                      {row.identity}
                    </span>
                    {!row.ok && row.problem && (
                      <p className="mt-1 flex items-start gap-1 text-xs text-red-700">
                        <AlertTriangle className="w-3.5 h-3.5 mt-px shrink-0" />
                        {row.problem}
                      </p>
                    )}
                  </td>
                  <td className="px-3 py-2.5 text-xs text-gray-600">{row.configured_in}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
