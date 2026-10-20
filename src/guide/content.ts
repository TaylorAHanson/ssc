/**
 * The in-app Guide's pages: markdown under `docs/guide/` plus a few admin
 * reference docs from `docs/`, bundled at build time (edit those files, not
 * this one). `backend/tests/unit/test_guide_links.py` reads the glob list below
 * and fails on any broken link or anchor.
 *
 * Pages may write `{{brand_name}}` / `{{brand_short_name}}`; the configured
 * brand is filled in at render time (same tokens as the backend's
 * `settings.apply_brand_tokens`).
 */

const RAW = import.meta.glob<string>(
  [
    '../../docs/guide/**/*.md',
    '../../docs/GOVERNANCE.md',
    '../../docs/PLATFORM_ADMINISTRATION.md',
    '../../docs/SERVICE_PRINCIPALS.md',
  ],
  { query: '?raw', import: 'default', eager: true },
);

export interface GuidePage {
  /** URL path under `/guide/` ('' for the guide's home page). */
  slug: string;
  /** Repo-relative path, e.g. `docs/guide/discover-data.md`. */
  file: string;
  title: string;
  source: string;
}

export interface NavSection {
  title: string;
  slugs: string[];
  /** Admin-only reading: collapsed by default for everyone else. */
  admin?: boolean;
  collapsed?: boolean;
}

export interface Brand {
  name: string;
  shortName: string;
}

const GUIDE_DIR = 'docs/guide/';

function slugFor(file: string): string {
  if (file.startsWith(GUIDE_DIR)) {
    const rel = file.slice(GUIDE_DIR.length).replace(/\.md$/, '');
    return rel === 'index' ? '' : rel.replace(/\/index$/, '');
  }
  const name = file.slice(file.lastIndexOf('/') + 1).replace(/\.md$/, '');
  return `reference/${name.toLowerCase().replace(/_/g, '-')}`;
}

function titleOf(source: string, file: string): string {
  const match = /^#\s+(.+)$/m.exec(source);
  return match ? headingText(match[1]) : file;
}

export const PAGES: GuidePage[] = Object.entries(RAW)
  .map(([path, source]) => {
    const file = path.replace(/^(\.\.\/)+/, '');
    return { slug: slugFor(file), file, title: titleOf(source, file), source };
  })
  .sort((a, b) => a.file.localeCompare(b.file));

const BY_SLUG = new Map(PAGES.map((p) => [p.slug, p]));
const BY_FILE = new Map(PAGES.map((p) => [p.file, p]));

export function pageBySlug(slug: string): GuidePage | undefined {
  return BY_SLUG.get(slug);
}

export const NAV_SECTIONS: NavSection[] = [
  { title: 'Get started', slugs: ['', 'getting-started'] },
  {
    title: 'Everyday tasks',
    slugs: ['using-the-assistant', 'discover-data', 'requests-and-approvals', 'community-and-training'],
  },
  {
    title: 'For admins',
    admin: true,
    slugs: [
      'admin',
      'admin/settings-and-access',
      'admin/workflows-and-tools',
      'admin/governance',
      'admin/metadata-manager',
      'admin/metadata-api',
      'admin/access-rules',
    ],
  },
  {
    title: 'Admin reference',
    admin: true,
    collapsed: true,
    slugs: ['reference/governance', 'reference/platform-administration', 'reference/service-principals'],
  },
];

/** Short sidebar labels where a page's own title is too long. */
export const NAV_LABELS: Record<string, string> = {
  '': 'Overview',
  admin: 'Admin overview',
  'reference/governance': 'Governance operating guide',
  'reference/platform-administration': 'Workflow administration',
  'reference/service-principals': 'Service principals & grants',
};

/** Pages missing from NAV_SECTIONS still get listed, so a new doc is never orphaned. */
export const NAV: NavSection[] = (() => {
  const listed = new Set(NAV_SECTIONS.flatMap((s) => s.slugs));
  const extra = PAGES.filter((p) => !listed.has(p.slug)).map((p) => p.slug);
  return extra.length ? [...NAV_SECTIONS, { title: 'More', slugs: extra }] : NAV_SECTIONS;
})();

/** Every page in sidebar order, for previous / next links. */
export const READING_ORDER: GuidePage[] = NAV.flatMap((s) => s.slugs)
  .map((slug) => BY_SLUG.get(slug))
  .filter((p): p is GuidePage => Boolean(p));

export function applyBrand(text: string, brand: Brand): string {
  return text.replaceAll('{{brand_name}}', brand.name).replaceAll('{{brand_short_name}}', brand.shortName);
}

/** Heading markdown reduced to its visible text (no code ticks, emphasis or link targets). */
export function headingText(raw: string): string {
  return raw
    .replace(/`([^`]*)`/g, '$1')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\*\*|__|\*/g, '')
    .trim();
}

/** GitHub-style anchor ids, so links into these docs work on GitHub too. */
export function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^\p{L}\p{N}_\- ]/gu, '')
    .replace(/ /g, '-');
}

export interface OutlineItem {
  id: string;
  text: string;
  depth: 2 | 3;
}

/** Lines outside fenced code blocks and HTML comments. */
function proseLines(source: string): string[] {
  const lines: string[] = [];
  let inFence = false;
  for (const line of source.replace(/<!--[\s\S]*?-->/g, '').split('\n')) {
    if (/^\s*(```|~~~)/.test(line)) {
      inFence = !inFence;
      continue;
    }
    if (!inFence) lines.push(line);
  }
  return lines;
}

const SECTION_HEADING = /^(#{2,3})\s+(.+?)\s*#*\s*$/;

export function outline(source: string): OutlineItem[] {
  const items: OutlineItem[] = [];
  for (const line of proseLines(source)) {
    const match = SECTION_HEADING.exec(line);
    if (match) {
      const text = headingText(match[2]);
      items.push({ id: slugify(text), text, depth: match[1].length as 2 | 3 });
    }
  }
  return items;
}

export interface PageSection {
  /** Anchor of the nearest h2/h3 above this text ('' before the first one). */
  anchor: string;
  text: string;
}

/** Plain-text chunks of a page, one per h2/h3 section, for search. */
function sections(source: string): PageSection[] {
  const out: PageSection[] = [{ anchor: '', text: '' }];
  for (const line of proseLines(source)) {
    const match = SECTION_HEADING.exec(line);
    if (match) out.push({ anchor: slugify(headingText(match[2])), text: '' });
    const plain = line
      .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
      .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
      .replace(/[`*_>#|]/g, '')
      .trim();
    if (plain) out[out.length - 1].text += `${plain} `;
  }
  return out.filter((s) => s.text);
}

const SECTIONS = new Map(PAGES.map((p) => [p.slug, sections(p.source)]));

export interface SearchHit {
  page: GuidePage;
  anchor: string;
  snippet: string;
}

export function searchGuide(query: string, brand: Brand, limit = 12): SearchHit[] {
  const q = query.trim().toLowerCase();
  if (q.length < 2) return [];
  const hits: SearchHit[] = [];
  for (const page of READING_ORDER) {
    for (const section of SECTIONS.get(page.slug) ?? []) {
      const text = applyBrand(section.text, brand);
      const at = text.toLowerCase().indexOf(q);
      if (at === -1) continue;
      const start = Math.max(0, at - 40);
      const snippet = `${start > 0 ? '…' : ''}${text.slice(start, at + q.length + 60).trim()}…`;
      hits.push({ page, anchor: section.anchor, snippet });
      break;
    }
    if (hits.length >= limit) break;
  }
  return hits;
}

export type ResolvedLink =
  | { kind: 'internal'; to: string }
  | { kind: 'external'; href: string }
  /** A repo file that isn't part of the guide (e.g. a developer doc). */
  | { kind: 'repo'; path: string };

function normalize(path: string): string {
  const out: string[] = [];
  for (const part of path.split('/')) {
    if (part === '..') out.pop();
    else if (part && part !== '.') out.push(part);
  }
  return out.join('/');
}

export function resolveLink(href: string, fromFile: string): ResolvedLink {
  if (/^[a-z][a-z0-9+.-]*:/i.test(href)) return { kind: 'external', href };
  if (href.startsWith('#') || href.startsWith('/')) return { kind: 'internal', to: href };
  const hashAt = href.indexOf('#');
  const target = hashAt === -1 ? href : href.slice(0, hashAt);
  const hash = hashAt === -1 ? '' : href.slice(hashAt);
  const path = normalize(`${fromFile.slice(0, fromFile.lastIndexOf('/') + 1)}${target}`);
  const page = BY_FILE.get(path);
  if (page) return { kind: 'internal', to: `/guide${page.slug ? `/${page.slug}` : ''}${hash}` };
  return { kind: 'repo', path };
}
