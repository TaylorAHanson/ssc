import { OTHER_DOMAIN } from '../services/api';

/** Anything carrying Databricks Discover domain membership (tag keys). */
export interface DomainMember {
  domains?: string[] | null;
  subdomains?: string[] | null;
}

const same = (a: string, b: string) => a.toLowerCase() === b.toLowerCase();

/** Whether `item` is in the domain keyed `domain` (a tag key, or `OTHER_DOMAIN` for none). */
export function inDomain(item: DomainMember, domain: string): boolean {
  const domains = item.domains ?? [];
  if (domain === OTHER_DOMAIN) return domains.length === 0;
  return domains.some((d) => same(d, domain));
}

/** Whether `item` is in the subdomain with tag key `subdomain`. */
export function inSubdomain(item: DomainMember, subdomain: string): boolean {
  return (item.subdomains ?? []).some((s) => same(s, subdomain));
}

/** A subdomain's name within its parent: `supplychain/procurement` → `procurement`. */
export function subdomainLabel(subdomain: string, domains: string[]): string {
  const parent = domains.find((d) => subdomain.toLowerCase().startsWith(`${d.toLowerCase()}/`));
  return parent ? subdomain.slice(parent.length + 1) : subdomain;
}

/**
 * Readable placements, e.g. `["Supply Chain › Procurement", "Plant Reliability"]`.
 * A domain reached only through one of its subdomains isn't listed twice.
 */
export function domainPaths(item: DomainMember): string[] {
  const domains = item.domains ?? [];
  const subdomains = item.subdomains ?? [];
  const covered = new Set<string>();
  const paths = subdomains.map((s) => {
    const parent = domains.find((d) => s.toLowerCase().startsWith(`${d.toLowerCase()}/`));
    if (parent) covered.add(parent.toLowerCase());
    return parent ? `${parent} › ${subdomainLabel(s, domains)}` : s;
  });
  return [...paths, ...domains.filter((d) => !covered.has(d.toLowerCase()))];
}
