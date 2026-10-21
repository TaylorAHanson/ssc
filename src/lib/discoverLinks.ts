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
 *   /discovery?domain=finance&show=legacy
 *
 * Domains and subdomains have no ids in the API, so they are addressed by a
 * slug of their name; matching slugifies both sides, so a raw name works too.
 * `show` (comma-separated content kinds) picks what a domain view opens on,
 * overriding the remembered choice for that visit.
 */

export const DISCOVER_PATH = '/discovery';

export const DISCOVER_CONTENT_KINDS = ['metrics', 'tables', 'dashboards', 'apps', 'legacy'] as const;
export type DiscoverContentKind = (typeof DISCOVER_CONTENT_KINDS)[number];

export interface DiscoverLinkTarget {
  /** Domain name or slug. */
  domain?: string | null;
  /** Subdomain name or slug; ignored without a domain. */
  subdomain?: string | null;
  /** Content kinds the domain view opens on; ignored without a domain. */
  show?: DiscoverContentKind[] | null;
  /** Asset id (e.g. a Unity Catalog full name). */
  asset?: string | null;
}

function parseContentKinds(value: string | null): DiscoverContentKind[] | null {
  if (!value) return null;
  const parts = value.split(',').map((p) => p.trim().toLowerCase());
  const kinds = DISCOVER_CONTENT_KINDS.filter((k) => parts.includes(k));
  return kinds.length ? kinds : null;
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
  const show = domain && target.show?.length ? DISCOVER_CONTENT_KINDS.filter((k) => target.show!.includes(k)) : [];
  if (domain) next.set('domain', domain);
  if (subdomain) next.set('subdomain', subdomain);
  if (show.length) next.set('show', show.join(','));
  if (target.asset) next.set('asset', target.asset);
  params.forEach((value, key) => {
    if (key !== 'domain' && key !== 'subdomain' && key !== 'show' && key !== 'asset') next.append(key, value);
  });
  return next;
}

export function readDiscoverParams(params: URLSearchParams): DiscoverLinkTarget {
  return {
    domain: params.get('domain') || null,
    subdomain: params.get('subdomain') || null,
    show: parseContentKinds(params.get('show')),
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
