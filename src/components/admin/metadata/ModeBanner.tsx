import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Notice } from '../../rbac/ui';
import { useMetadata } from './context';
import { formatTime, relativeTime } from './format';

/** Whether saved changes reach Unity Catalog right now, in plain language. */
export function SyncStatus() {
  const { config } = useMetadata();
  const sweep = config.last_sweep_at ? (
    <span title={formatTime(config.last_sweep_at)}> Unity Catalog was last checked {relativeTime(config.last_sweep_at)}.</span>
  ) : (
    <span> Unity Catalog hasn’t been checked yet.</span>
  );
  const managed = config.managed_catalogs.length ? (
    <span className="mt-1 block">
      Catalogs kept in sync: {config.managed_catalogs.includes('*') ? 'all of them' : config.managed_catalogs.join(', ')}.
    </span>
  ) : null;
  const settings = (
    <span className="mt-1 block">
      Change this under{' '}
      <Link to="/admin/settings" className="font-medium underline">
        Admin → Settings
      </Link>{' '}
      (Metadata Manager).
    </span>
  );
  if (config.reconcile_mode === 'enforce') {
    return (
      <Notice kind="success" title="Keeping Unity Catalog in sync">
        Changes saved here are written to Unity Catalog within about a minute, and changes made there directly are put back, except for tags another tool manages.
        {sweep}
        {managed}
        {settings}
      </Notice>
    );
  }
  if (config.reconcile_mode === 'plan') {
    return (
      <Notice kind="warn" title="Report only">
        {config.enforce_blocked_reason ?? 'Changes are saved here, and differences from Unity Catalog are reported, but nothing is written to Unity Catalog yet.'}
        {sweep}
        {managed}
        {settings}
      </Notice>
    );
  }
  return (
    <Notice kind="warn" title="Syncing is off">
      Changes are saved here but never written to Unity Catalog, and Unity Catalog isn’t checked.
      {settings}
    </Notice>
  );
}

export function CircuitBreakerBanner({ action }: { action?: ReactNode }) {
  const { config } = useMetadata();
  if (!config.circuit_breaker_tripped) return null;
  return (
    <Notice kind="error" title="Syncing paused itself">
      <p>
        A sync would have removed more tags or descriptions than the safety limit allows, so it stopped before writing anything. Changes are still saved here, but nothing
        reaches Unity Catalog until an admin reviews the removals and resumes.
      </p>
      {action && <div className="mt-2">{action}</div>}
    </Notice>
  );
}
