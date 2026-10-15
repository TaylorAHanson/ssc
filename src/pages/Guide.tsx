/**
 * Guide — the in-app user guide at `/guide/*`.
 *
 * Pages are markdown bundled from `docs/guide/` (see `guide/content`). Links
 * between pages stay inside the app, every heading is deep-linkable through
 * the URL hash, and the right-hand rail outlines the current page.
 */
import { useEffect, useMemo, useState } from 'react';
import { Link, NavLink, useLocation, useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, ArrowRight, ChevronRight, Search } from 'lucide-react';
import { Card, CardContent } from '../components/ui/card';
import { cn } from '../lib/utils';
import { GuideMarkdown } from '../guide/GuideMarkdown';
import { NAV, NAV_LABELS, READING_ORDER, applyBrand, outline, pageBySlug, searchGuide } from '../guide/content';
import type { Brand, NavSection } from '../guide/content';
import { useBrandingStore } from '../stores/brandingStore';
import { useUserStore } from '../stores/userStore';

const ADMIN_PERSONAS = new Set(['Platform Admin', 'Governance Admin', 'Security Admin', 'Finance Admin']);

function pathFor(slug: string, anchor = ''): string {
  return `/guide${slug ? `/${slug}` : ''}${anchor ? `#${anchor}` : ''}`;
}

function hashId(hash: string): string {
  try {
    return decodeURIComponent(hash.slice(1));
  } catch {
    return hash.slice(1);
  }
}

function navLabel(slug: string, brand: Brand): string {
  return NAV_LABELS[slug] ?? applyBrand(pageBySlug(slug)?.title ?? slug, brand);
}

interface SectionNavProps {
  section: NavSection;
  currentSlug: string;
  defaultOpen: boolean;
  brand: Brand;
}

function SectionNav({ section, currentSlug, defaultOpen, brand }: SectionNavProps) {
  const [toggled, setToggled] = useState<boolean | null>(null);
  const open = toggled ?? (defaultOpen || section.slugs.includes(currentSlug));
  return (
    <div>
      <button
        type="button"
        onClick={() => setToggled(!open)}
        aria-expanded={open}
        className="w-full flex items-center gap-1 px-3 py-1 text-xs font-semibold text-gray-400 uppercase tracking-wider hover:text-gray-600"
      >
        <ChevronRight className={cn('w-3 h-3 transition-transform', open && 'rotate-90')} />
        {section.title}
      </button>
      {open && (
        <ul className="mt-1 space-y-0.5">
          {section.slugs
            .filter((slug) => pageBySlug(slug))
            .map((slug) => (
              <li key={slug}>
                <NavLink
                  to={pathFor(slug)}
                  end
                  className={({ isActive }) =>
                    cn(
                      'block px-3 py-1.5 text-sm rounded-md transition-colors',
                      isActive
                        ? 'bg-accent-soft text-accent font-medium'
                        : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900',
                    )
                  }
                >
                  {navLabel(slug, brand)}
                </NavLink>
              </li>
            ))}
        </ul>
      )}
    </div>
  );
}

export function Guide() {
  const params = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const brandName = useBrandingStore((s) => s.brandName);
  const brandShortName = useBrandingStore((s) => s.brandShortName);
  const persona = useUserStore((s) => s.currentPersona);
  const [query, setQuery] = useState('');

  const slug = (params['*'] ?? '').replace(/\/+$/, '').replace(/\.md$/, '');
  const page = pageBySlug(slug);
  const brand = useMemo<Brand>(
    () => ({ name: brandName, shortName: brandShortName || brandName }),
    [brandName, brandShortName],
  );
  const headings = useMemo(() => (page ? outline(page.source) : []), [page]);
  const hits = useMemo(() => searchGuide(query, brand), [query, brand]);
  const isAdmin = ADMIN_PERSONAS.has(persona);

  const position = page ? READING_ORDER.indexOf(page) : -1;
  const previous = position > 0 ? READING_ORDER[position - 1] : undefined;
  const next = position >= 0 ? READING_ORDER[position + 1] : undefined;

  const activeHeading = hashId(location.hash);

  useEffect(() => {
    const target = activeHeading ? document.getElementById(activeHeading) : null;
    if (target) target.scrollIntoView({ block: 'start' });
    else document.querySelector('main')?.scrollTo({ top: 0 });
  }, [slug, activeHeading, location.key]);

  return (
    <div className="grid grid-cols-1 md:grid-cols-[14rem_minmax(0,1fr)] xl:grid-cols-[14rem_minmax(0,1fr)_12rem] gap-6 lg:gap-8 items-start">
      <aside className="md:sticky md:top-0 md:max-h-[calc(100vh-3rem)] md:overflow-y-auto custom-scrollbar space-y-4">
        <div className="relative">
          <Search className="w-4 h-4 absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search the guide"
            aria-label="Search the guide"
            className="w-full rounded-md border border-gray-200 bg-white py-1.5 pl-8 pr-2 text-sm focus:outline-none focus:ring-2 focus:ring-accent/40"
          />
        </div>

        {query.trim().length >= 2 ? (
          <ul className="space-y-1" aria-label="Search results">
            {hits.length === 0 && <li className="px-3 text-sm text-gray-500">No matches.</li>}
            {hits.map((hit) => (
              <li key={hit.page.slug}>
                <Link
                  to={pathFor(hit.page.slug, hit.anchor)}
                  onClick={() => setQuery('')}
                  className="block rounded-md px-3 py-2 hover:bg-gray-100"
                >
                  <span className="block text-sm font-medium text-gray-900">{navLabel(hit.page.slug, brand)}</span>
                  <span className="block text-xs text-gray-500 line-clamp-2">{hit.snippet}</span>
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <>
            <nav aria-label="Guide pages" className="hidden md:block space-y-3">
              {NAV.map((section) => (
                <SectionNav
                  key={section.title}
                  section={section}
                  currentSlug={slug}
                  defaultOpen={section.admin ? isAdmin && !section.collapsed : !section.collapsed}
                  brand={brand}
                />
              ))}
            </nav>
            <select
              aria-label="Guide page"
              value={page ? slug : ''}
              onChange={(e) => navigate(pathFor(e.target.value))}
              className="md:hidden w-full rounded-md border border-gray-200 bg-white px-2 py-1.5 text-sm"
            >
              {NAV.map((section) => (
                <optgroup key={section.title} label={section.title}>
                  {section.slugs
                    .filter((s) => pageBySlug(s))
                    .map((s) => (
                      <option key={s} value={s}>
                        {navLabel(s, brand)}
                      </option>
                    ))}
                </optgroup>
              ))}
            </select>
          </>
        )}
      </aside>

      <article className="min-w-0">
        {page ? (
          <>
            <GuideMarkdown page={page} brandName={brand.name} brandShortName={brand.shortName} />
            {(previous || next) && (
              <nav aria-label="Previous and next pages" className="mt-10 pt-6 border-t border-gray-200 flex justify-between gap-4">
                {previous ? (
                  <Link to={pathFor(previous.slug)} className="flex items-center gap-2 text-sm text-gray-600 hover:text-accent">
                    <ArrowLeft className="w-4 h-4" />
                    {navLabel(previous.slug, brand)}
                  </Link>
                ) : (
                  <span />
                )}
                {next && (
                  <Link to={pathFor(next.slug)} className="flex items-center gap-2 text-sm text-gray-600 hover:text-accent text-right">
                    {navLabel(next.slug, brand)}
                    <ArrowRight className="w-4 h-4" />
                  </Link>
                )}
              </nav>
            )}
          </>
        ) : (
          <Card className="bg-gray-50">
            <CardContent className="py-12 text-center text-gray-500 space-y-2">
              <p>This guide page doesn't exist. It may have moved.</p>
              <Link to="/guide" className="text-accent hover:underline">
                Go to the guide's home page
              </Link>
            </CardContent>
          </Card>
        )}
      </article>

      {headings.length > 1 && (
        <nav aria-label="On this page" className="hidden xl:block xl:sticky xl:top-0 xl:max-h-[calc(100vh-3rem)] xl:overflow-y-auto custom-scrollbar">
          <p className="px-3 mb-2 text-xs font-semibold text-gray-400 uppercase tracking-wider">On this page</p>
          <ul className="space-y-0.5 border-l border-gray-200">
            {headings.map((h) => (
              <li key={h.id}>
                <Link
                  to={{ hash: `#${h.id}` }}
                  className={cn(
                    'block py-1 text-xs -ml-px border-l transition-colors',
                    h.depth === 3 ? 'pl-6' : 'pl-3',
                    activeHeading === h.id
                      ? 'border-accent text-accent font-medium'
                      : 'border-transparent text-gray-500 hover:text-gray-900',
                  )}
                >
                  {applyBrand(h.text, brand)}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
      )}
    </div>
  );
}
