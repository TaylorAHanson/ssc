import type { LegacyMapping, LegacyStatus } from '../../services/api';
import { domainPaths } from '../../lib/discoverDomains';

export const LEGACY_STATUSES: LegacyStatus[] = ['Active', 'Migrating', 'Deprecated'];

export function prettyMetricViewName(name: string) {
  return name.replace(/^(metric_|sem_)/, '').replace(/_metric_view$/, '');
}

/** Case-insensitive match of a search term against what a user would remember about a mapping. */
export function legacyMatches(m: LegacyMapping, term: string) {
  const t = term.trim().toLowerCase();
  if (!t) return true;
  return `${m.dashboard} ${m.owner ?? ''} ${m.metric_view} ${m.description ?? ''} ${domainPaths(m).join(' ')}`
    .toLowerCase()
    .includes(t);
}
