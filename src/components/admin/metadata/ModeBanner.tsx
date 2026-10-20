import type { ReactNode } from 'react';
import { Notice } from '../../rbac/ui';
import { useMetadata } from './context';
import { formatTime, relativeTime } from './format';

/** What the reconciler does with drift right now, in plain language. */
export function ModeBanner() {
  const { config } = useMetadata();
  const sweep = config.last_sweep_at ? (
    <span title={formatTime(config.last_sweep_at)}> Last full check {relativeTime(config.last_sweep_at)}.</span>
  ) : null;
  if (config.reconcile_mode === 'enforce') {
    return (
      <Notice kind="info" title="Enforce mode">
        The reconciler writes declared tags and descriptions to Unity Catalog and undoes changes made there directly, except for keys managed elsewhere.
        {sweep}
      </Notice>
    );
  }
  if (config.reconcile_mode === 'plan') {
    return (
      <Notice kind="warn" title="Plan mode">
        The reconciler only reports what it would change. Nothing is written to Unity Catalog, so drift stays until an admin switches to enforce mode.
        {sweep}
      </Notice>
    );
  }
  return (
    <Notice kind="warn" title="Reconciler off">
      Declared metadata is saved here but never applied to Unity Catalog, and drift isn’t checked.
    </Notice>
  );
}

export function CircuitBreakerBanner({ action }: { action?: ReactNode }) {
  const { config } = useMetadata();
  if (!config.circuit_breaker_tripped) return null;
  return (
    <Notice kind="error" title="The reconciler stopped itself">
      <p>
        A run would have removed more tags than the safety limit allows, so it paused instead of writing anything. Declared changes are still saved, but nothing
        reaches Unity Catalog until an admin reviews the planned removals and resumes.
      </p>
      {action && <div className="mt-2">{action}</div>}
    </Notice>
  );
}
