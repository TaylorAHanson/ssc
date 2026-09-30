/**
 * Deep links into the Discover page. Selection lives in query params on
 * `/discovery` (not path segments) because the asset panel is an overlay that
 * can open over any view — including catalog-wide search results where there is
 * no domain — and asset ids are dotted UC names:
 *
 *   /discovery?domain=supply-chain
 *   /discovery?domain=supply-chain&subdomain=planning-and-forecasting
 *   /discovery?domain=supply-chain&subdomain=planning-and-forecasting&asset=main.sc.demand_mv
 *   /discovery?asset=main.sales.orders
 *
 * Domains and subdomains have no ids in the API, so they are addressed by a
 * slug of their name; matching slugifies both sides, so a raw name works too.
 */

export const DISCOVER_PATH = '/discovery';

export interface DiscoverLinkTarget {
  /** Domain name or slug. */
  domain?: string | null;
  /** Subdomain name or slug; ignored without a domain. */
  subdomain?: string | null;
  /** Asset id (e.g. a Unity Catalog full name). */
  asset?: string | null;
}

export function discoverSlug(name: string): string {
  const slug = name
    .toLowerCase()
    .replace(/&/g, ' and ')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return slug || name.trim();
}

export function matchesSlug(name: string, value: string): boolean {
  return discoverSlug(name) === discoverSlug(value);
}

/** Selection keys in `params`, normalised to slugs; everything else is kept. */
export function withDiscoverParams(params: URLSearchParams, target: DiscoverLinkTarget): URLSearchParams {
  const next = new URLSearchParams();
  const domain = target.domain ? discoverSlug(target.domain) : null;
  const subdomain = domain && target.subdomain ? discoverSlug(target.subdomain) : null;
  if (domain) next.set('domain', domain);
  if (subdomain) next.set('subdomain', subdomain);
  if (target.asset) next.set('asset', target.asset);
  params.forEach((value, key) => {
    if (key !== 'domain' && key !== 'subdomain' && key !== 'asset') next.append(key, value);
  });
  return next;
}

export function readDiscoverParams(params: URLSearchParams): DiscoverLinkTarget {
  return {
    domain: params.get('domain') || null,
    subdomain: params.get('subdomain') || null,
    asset: params.get('asset') || null,
  };
}

/** In-app path, e.g. for `navigate()` or a router `<Link>`. */
export function buildDiscoverLink(target: DiscoverLinkTarget = {}): string {
  const search = withDiscoverParams(new URLSearchParams(), target).toString();
  return search ? `${DISCOVER_PATH}?${search}` : DISCOVER_PATH;
}

/** Absolute URL for sharing (clipboard, email). */
export function absoluteDiscoverLink(target: DiscoverLinkTarget = {}): string {
  return new URL(buildDiscoverLink(target), window.location.origin).toString();
}
