/**
 * Release notes — every version on one page, with a version nav on the left.
 *
 * Content comes from the repo-root `RELEASE_NOTES.md` (see `lib/releaseNotes`).
 * Each version section is deep-linkable via its URL hash (`#v1.0.0`), and the
 * nav follows the reader's scroll position.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import type { MouseEvent } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { format, isValid, parseISO } from 'date-fns';
import { Card, CardContent } from '../components/ui/card';
import { cn } from '../lib/utils';
import { renderMarkdownSafe } from '../lib/markdown';
import { CURRENT_VERSION, RELEASE_NOTES, releaseAnchorId } from '../lib/releaseNotes';
import { useBrandingStore } from '../stores/brandingStore';

// How long a section stays highlighted after it's jumped to.
const HIGHLIGHT_MS = 2000;
// Scroll-spy ignores intersection changes for this long after a jump, so the
// smooth scroll passing over other sections doesn't flicker the nav.
const SCROLL_LOCK_MS = 1000;

function scrollLockDeadline(): number {
  return Date.now() + SCROLL_LOCK_MS;
}

function versionFromHash(hash: string): string | null {
  const id = decodeURIComponent(hash.replace(/^#/, ''));
  return RELEASE_NOTES.find((r) => releaseAnchorId(r.version) === id)?.version ?? null;
}

function formatReleaseDate(date: string | null): string | null {
  if (!date) return null;
  const parsed = parseISO(date);
  return isValid(parsed) ? format(parsed, 'MMMM d, yyyy') : date;
}

function scrollToRelease(version: string) {
  const el = document.getElementById(releaseAnchorId(version));
  if (!el) return;
  const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  el.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'start' });
}

function CurrentBadge() {
  return (
    <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-primary/10 text-primary">
      Current
    </span>
  );
}

export function ReleaseNotes() {
  const brandName = useBrandingStore((s) => s.brandName);
  const location = useLocation();
  const navigate = useNavigate();
  const contentRef = useRef<HTMLDivElement>(null);
  const scrollLockUntilRef = useRef(0);
  const skipHashScrollRef = useRef(false);

  const [activeVersion, setActiveVersion] = useState<string | null>(
    () => versionFromHash(location.hash) ?? CURRENT_VERSION,
  );
  const [highlightedVersion, setHighlightedVersion] = useState<string | null>(
    () => versionFromHash(location.hash),
  );

  const releases = useMemo(
    () =>
      RELEASE_NOTES.map((r) => ({
        ...r,
        anchorId: releaseAnchorId(r.version),
        displayDate: formatReleaseDate(r.date),
        html: renderMarkdownSafe(r.body),
      })),
    [],
  );

  // Deep links: scroll to the version named in the URL hash.
  useEffect(() => {
    if (skipHashScrollRef.current) {
      skipHashScrollRef.current = false;
      return;
    }
    const version = versionFromHash(location.hash);
    if (!version) return;
    scrollLockUntilRef.current = scrollLockDeadline();
    scrollToRelease(version);
  }, [location.hash]);

  useEffect(() => {
    if (!highlightedVersion) return;
    const timer = window.setTimeout(() => setHighlightedVersion(null), HIGHLIGHT_MS);
    return () => window.clearTimeout(timer);
  }, [highlightedVersion]);

  // Scroll-spy: the active version is the first section inside the top 30% of
  // the viewport; while a long section fills the view, the last one sticks.
  useEffect(() => {
    const container = contentRef.current;
    if (!container || typeof IntersectionObserver === 'undefined') return;
    const visible = new Set<string>();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const version = (entry.target as HTMLElement).dataset.releaseVersion;
          if (!version) continue;
          if (entry.isIntersecting) visible.add(version);
          else visible.delete(version);
        }
        if (Date.now() < scrollLockUntilRef.current) return;
        const first = RELEASE_NOTES.find((r) => visible.has(r.version));
        if (first) setActiveVersion(first.version);
      },
      { rootMargin: '0px 0px -70% 0px' },
    );
    container
      .querySelectorAll<HTMLElement>('[data-release-version]')
      .forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, []);

  const handleNavClick = (event: MouseEvent<HTMLAnchorElement>, version: string) => {
    event.preventDefault();
    setActiveVersion(version);
    setHighlightedVersion(version);
    scrollLockUntilRef.current = scrollLockDeadline();
    scrollToRelease(version);
    const hash = `#${releaseAnchorId(version)}`;
    if (location.hash !== hash) {
      skipHashScrollRef.current = true;
      navigate({ hash }, { replace: true });
    }
  };

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-3xl font-bold text-gray-900 mb-2">Release notes</h1>
        <p className="text-gray-600">What's new in {brandName}, newest first.</p>
      </div>

      {releases.length === 0 ? (
        <Card className="bg-gray-50">
          <CardContent className="py-12 text-center text-gray-500">
            No release notes have been published yet.
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-[12rem_minmax(0,1fr)] gap-6 lg:gap-8 items-start">
          <nav aria-label="Versions" className="lg:sticky lg:top-0">
            <p className="hidden lg:block px-3 mb-2 text-xs font-semibold text-gray-400 uppercase tracking-wider">
              Versions
            </p>
            <ul className="flex lg:flex-col gap-1 overflow-x-auto pb-1 lg:pb-0">
              {releases.map((r) => {
                const isActive = r.version === activeVersion;
                return (
                  <li key={r.version} className="shrink-0">
                    <a
                      href={`#${r.anchorId}`}
                      onClick={(event) => handleNavClick(event, r.version)}
                      aria-current={isActive ? 'location' : undefined}
                      className={cn(
                        'flex items-center justify-between gap-2 px-3 py-2 rounded-md text-sm whitespace-nowrap transition-colors',
                        isActive
                          ? 'bg-accent-soft text-accent font-semibold'
                          : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900',
                      )}
                    >
                      <span>v{r.version}</span>
                      {r.version === CURRENT_VERSION && <CurrentBadge />}
                    </a>
                  </li>
                );
              })}
            </ul>
          </nav>

          <div ref={contentRef} className="space-y-6 min-w-0">
            {releases.map((r) => (
              <section
                key={r.version}
                id={r.anchorId}
                data-release-version={r.version}
                aria-labelledby={`${r.anchorId}-heading`}
                className={cn(
                  'scroll-mt-6 rounded-lg border bg-white shadow-sm transition-shadow duration-500',
                  r.version === highlightedVersion
                    ? 'border-primary ring-2 ring-primary/30'
                    : 'border-gray-200',
                )}
              >
                <header className="flex flex-wrap items-center gap-x-3 gap-y-1 px-6 pt-5 pb-4 border-b border-gray-100">
                  <h2 id={`${r.anchorId}-heading`} className="text-xl font-semibold text-gray-900">
                    Version {r.version}
                  </h2>
                  {r.version === CURRENT_VERSION && <CurrentBadge />}
                  {r.displayDate && (
                    <time dateTime={r.date ?? undefined} className="text-sm text-gray-500 sm:ml-auto">
                      {r.displayDate}
                    </time>
                  )}
                </header>
                <div
                  className="px-6 py-5 prose prose-sm max-w-none text-gray-700 [&_h3]:mt-6 [&_h3]:mb-2 [&_h3]:text-xs [&_h3]:font-semibold [&_h3]:uppercase [&_h3]:tracking-wider [&_h3]:text-gray-500 [&>h3:first-child]:mt-0"
                  dangerouslySetInnerHTML={{ __html: r.html }}
                />
              </section>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
