import React, { useMemo } from 'react';
import { Link } from 'react-router-dom';
import {
  AppWindow,
  ArrowRight,
  BarChart,
  Bell,
  Calendar,
  CheckCircle2,
  Compass,
  Database,
  ExternalLink,
  FileText,
  GraduationCap,
  Home as HomeIcon,
  List,
  MapPin,
  MessageSquare,
  Search,
  Sparkles,
  Users,
  WandSparkles,
  Wrench,
} from 'lucide-react';
import { useBrandingStore } from '../stores/brandingStore';
import type { EmbeddedApp } from '../stores/brandingStore';
import { useUserStore } from '../stores/userStore';
import { genieHomeUrl, workspaceHomeUrl } from '../lib/databricksLinks';
import { renderNavIcon } from '../lib/navIcons';
import type { UserPersona } from '../types';

// --------------------------------------------------------------------------
// Welcome page content model
//
// The masonry cards are generated from this static definition rather than
// being shared with the Sidebar component, because the Welcome page wants
// richer per-item descriptions and a per-group blurb that don't belong in
// the rail's compact icon labels. Group + item shapes mirror the sidebar's
// `NavItem` so it's easy to keep them in sync by hand when nav changes.
// --------------------------------------------------------------------------

type WelcomeItem = {
  id: string;
  title: string;
  description: string;
  icon: React.ReactNode;
  to?: string;
  href?: string;
  /** When set, item is only shown when the URL is configured. */
  requireUrl?: string | null;
  /** Optional persona gate, mirrors Sidebar's `allowedPersonas`. */
  allowedPersonas?: UserPersona[];
};

type WelcomeGroup = {
  id: string;
  title: string;
  description: string;
  icon: React.ReactNode;
  accentClass: string; // tailwind text/bg color pair for the group glyph
  items: WelcomeItem[];
};

const ICON_CLASS = 'w-4 h-4';

function buildGroups(opts: {
  workspaceUrl: string;
  embeddedApps: EmbeddedApp[];
  currentPersona: UserPersona;
}): WelcomeGroup[] {
  const genieUrl = genieHomeUrl(opts.workspaceUrl);
  const lakehouseUrl = workspaceHomeUrl(opts.workspaceUrl);

  const groups: WelcomeGroup[] = [
    {
      id: 'discover',
      title: 'Discover & Analyze',
      description:
        'Find the data, dashboards, and AI assistants already available across the platform.',
      icon: <Compass className="w-5 h-5" />,
      accentClass: 'bg-blue-50 text-blue-600',
      items: [
        {
          id: 'data_discovery',
          title: 'View & Search Catalog',
          description:
            'Browse the data catalog, search assets, and explore lineage.',
          icon: <Search className={ICON_CLASS} />,
          to: '/discovery',
        },
        {
          id: 'databricks_genie',
          title: 'Analyze and Explore',
          description:
            'Ask natural-language questions of your Databricks data.',
          icon: <Sparkles className={ICON_CLASS} />,
          href: genieUrl ?? undefined,
          requireUrl: genieUrl,
        },
      ],
    },
    {
      id: 'self-service',
      title: 'Requests & Approvals',
      description:
        'Request new data, access, or workspaces — and track everything you have in flight.',
      icon: <HomeIcon className="w-5 h-5" />,
      accentClass: 'bg-emerald-50 text-emerald-600',
      items: [
        {
          id: 'home',
          title: 'Request',
          description:
            'Start a new request for data, access, or a workspace.',
          icon: <WandSparkles className={ICON_CLASS} />,
          to: '/request',
        },
        {
          id: 'my_requests',
          title: 'My Requests',
          description:
            'Track the status of every request you have submitted.',
          icon: <List className={ICON_CLASS} />,
          to: '/requests',
        },
        {
          id: 'pending_approvals',
          title: 'Pending Approvals',
          description:
            'Review and approve requests waiting on you.',
          icon: <CheckCircle2 className={ICON_CLASS} />,
          to: '/approvals',
        },
        {
          id: 'reports',
          title: 'Reports',
          description:
            'See usage metrics and request reports at a glance.',
          icon: <BarChart className={ICON_CLASS} />,
          to: '/reports',
        },
      ],
    },
    {
      id: 'build-deploy',
      title: 'Build & Customize',
      description:
        'Jump into the building blocks of the lakehouse and the companion operations tooling.',
      icon: <Wrench className="w-5 h-5" />,
      accentClass: 'bg-indigo-50 text-indigo-600',
      items: [
        {
          id: 'databricks_lakehouse',
          title: 'Lakehouse',
          description:
            'Jump straight to your Databricks workspace.',
          icon: <Database className={ICON_CLASS} />,
          href: lakehouseUrl ?? undefined,
          requireUrl: lakehouseUrl,
        },
      ],
    },
    {
      id: 'community',
      title: 'Learn & Share',
      description:
        'Learn the platform, find templates, and connect with the rest of the data community.',
      icon: <Users className="w-5 h-5" />,
      accentClass: 'bg-amber-50 text-amber-600',
      items: [
        {
          id: 'training',
          title: 'Training',
          description:
            'Self-paced courses to skill up on the platform.',
          icon: <GraduationCap className={ICON_CLASS} />,
          to: '/community/training',
        },
        {
          id: 'event_calendar',
          title: 'Event Calendar',
          description:
            'Upcoming workshops, office hours, and town halls.',
          icon: <Calendar className={ICON_CLASS} />,
          to: '/community/events',
        },
        {
          id: 'templates_assets',
          title: 'Templates & Assets',
          description:
            'Reusable templates, dashboards, and starter projects.',
          icon: <FileText className={ICON_CLASS} />,
          to: '/community/assets',
        },
        {
          id: 'community_links',
          title: 'Community Links',
          description:
            'Curated channels, docs, and external resources.',
          icon: <MessageSquare className={ICON_CLASS} />,
          to: '/community/links',
        },
      ],
    },
  ];

  // Merge config-driven embedded apps into the welcome grid. Each app lands
  // in the group named by its `group` field (default "Build & Customize"),
  // creating that group on the fly if the welcome page doesn't already define
  // it. Persona-gated apps are hidden from users who lack the role.
  for (const app of opts.embeddedApps) {
    if (
      app.allowedPersonas &&
      !app.allowedPersonas.includes(opts.currentPersona)
    ) {
      continue;
    }
    const item: WelcomeItem = {
      id: app.id,
      title: app.title,
      description: app.description || `Open the embedded ${app.title} app.`,
      icon: renderNavIcon(app.icon, ICON_CLASS),
      to: `/embedded/${app.id}`,
      allowedPersonas: app.allowedPersonas,
    };
    const target = groups.find((g) => g.title === app.group);
    if (target) {
      target.items.push(item);
    } else {
      groups.push({
        id: `embedded-${app.group.replace(/\s+/g, '-').toLowerCase()}`,
        title: app.group,
        description: 'Companion apps embedded into this portal.',
        icon: <AppWindow className="w-5 h-5" />,
        accentClass: 'bg-indigo-50 text-indigo-600',
        items: [item],
      });
    }
  }

  return groups;
}

// Build the flat list of internal targets the user can pick as their
// default landing page. We exclude `/` itself (since that's where they
// already start) and external destinations (which can't be landed on).
function buildLandingChoices(groups: WelcomeGroup[]) {
  const choices: { value: string; label: string }[] = [
    { value: '', label: 'Welcome page (no preference)' },
  ];
  for (const group of groups) {
    for (const item of group.items) {
      if (item.to) {
        choices.push({ value: item.to, label: `${group.title} · ${item.title}` });
      }
    }
  }
  return choices;
}

export function Welcome() {
  const brandName = useBrandingStore((s) => s.brandName);
  const workspaceUrl = useBrandingStore((s) => s.databricksWorkspaceUrl);
  const embeddedApps = useBrandingStore((s) => s.embeddedApps);
  const currentPersona = useUserStore((s) => s.currentPersona);

  const defaultHomePage = useUserStore((s) => s.defaultHomePage);
  const setDefaultHomePage = useUserStore((s) => s.setDefaultHomePage);

  const groups = useMemo(
    () =>
      buildGroups({
        workspaceUrl,
        embeddedApps,
        currentPersona,
      }).map((group) => ({
        ...group,
        items: group.items.filter((item) => {
          // Hide items gated on a URL when that URL isn't configured.
          if (item.requireUrl !== undefined && !item.requireUrl) return false;
          return true;
        }),
      })),
    [workspaceUrl, embeddedApps, currentPersona]
  );

  // Drop entire groups that ended up empty after filtering.
  const visibleGroups = useMemo(
    () => groups.filter((group) => group.items.length > 0),
    [groups]
  );

  const landingChoices = useMemo(() => buildLandingChoices(groups), [groups]);

  return (
    // Full-width: let the page fill whatever main column it's in instead
    // of being capped at max-w-6xl. The masonry breakpoints (below) scale
    // the number of columns up so wide displays don't feel empty.
    <div className="space-y-8">
      {/* ─── Hero ─────────────────────────────────────────────── */}
      <section>
        <h1 className="text-3xl sm:text-4xl font-bold text-heading">
          Welcome to the {brandName}
        </h1>
      </section>

      {/* ─── Landing-page preference (prominent card) ─────────── */}
      <section
        className="rounded-2xl border border-accent-soft bg-linear-to-r from-accent-soft/60 to-white p-5 sm:p-6 shadow-sm"
        aria-labelledby="landing-pref-heading"
      >
        <div className="flex items-start gap-4">
          <div className="w-12 h-12 rounded-xl bg-accent text-white flex items-center justify-center shrink-0 shadow-sm">
            <MapPin className="w-6 h-6" />
          </div>
          <div className="flex-1 min-w-0">
            <h2
              id="landing-pref-heading"
              className="text-lg sm:text-xl font-semibold text-heading"
            >
              Want to get straight to work next time?
            </h2>
            <p className="mt-0.5 text-sm text-gray-600">
              Pick your home base and we'll take you there the moment you load
              the app.
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <label
                htmlFor="default-landing-page"
                className="text-sm font-medium text-gray-700"
              >
                Take me to
              </label>
              <select
                id="default-landing-page"
                value={defaultHomePage}
                onChange={(e) => setDefaultHomePage(e.target.value)}
                className="rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-800 shadow-sm focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/30"
              >
                {landingChoices.map((choice) => (
                  <option key={choice.value || 'none'} value={choice.value}>
                    {choice.label}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </div>
      </section>

      {/* ─── Masonry of capability groups ─────────────────────── */}
      <section>
        <div className="flex items-end justify-between gap-4 mb-4">
          <div>
            <h2 className="text-lg font-semibold text-heading">
              What's available
            </h2>
            <p className="mt-0.5 text-sm text-gray-600">
              Everything you can do from this portal, grouped by area.
            </p>
          </div>
          <span className="hidden sm:inline-flex items-center gap-1.5 text-xs text-gray-500">
            <Bell className="w-3.5 h-3.5" />
            Available to you
          </span>
        </div>

        {/* CSS Grid with `auto-fit + minmax` instead of CSS columns:
            cards always render at >= 320px wide, and the grid packs as
            many as will fit per row, expanding each to share the leftover
            width. Avoids the CSS-columns pitfall where 4 cards in a
            3-column flow leaves an entire column empty. */}
        <div className="grid grid-cols-[repeat(auto-fit,minmax(320px,1fr))] gap-4 items-start">
          {visibleGroups.map((group) => (
            <article
              key={group.id}
              // Outer category box: navy heading, light tinted fill so it
              // reads as a distinct surface vs. the item chips nested
              // inside.
              className="rounded-2xl border border-gray-200 bg-surface-muted p-5 shadow-sm hover:shadow-md transition-shadow"
            >
              <header className="flex items-start gap-3 pb-4 mb-4 border-b border-gray-200/70">
                <div
                  className={`w-11 h-11 rounded-xl flex items-center justify-center shrink-0 shadow-sm ${group.accentClass}`}
                >
                  {group.icon}
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-[10px] font-bold uppercase tracking-[0.12em] text-gray-500">
                    Category
                  </p>
                  <h3 className="text-lg font-bold text-heading leading-tight">
                    {group.title}
                  </h3>
                  <p className="mt-1 text-sm text-gray-600 leading-snug">
                    {group.description}
                  </p>
                </div>
              </header>

              <ul className="space-y-2">
                {group.items.map((item) => {
                  // Inner item box: white tile with its own border so it
                  // visually reads as a chip inside the category, not as
                  // a peer row alongside the category header.
                  const body = (
                    <div className="flex items-start gap-3 rounded-lg border border-gray-200 bg-white p-3 hover:border-accent hover:shadow-sm transition-all">
                      <div
                        className={`mt-0.5 w-8 h-8 rounded-md flex items-center justify-center shrink-0 ${group.accentClass}`}
                      >
                        {item.icon}
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-semibold text-heading flex items-center gap-1.5">
                          <span className="truncate">{item.title}</span>
                          {item.href && (
                            <ExternalLink className="w-3 h-3 text-gray-400 shrink-0" />
                          )}
                        </p>
                        <p className="mt-0.5 text-xs text-gray-500 leading-snug">
                          {item.description}
                        </p>
                      </div>
                      <ArrowRight className="w-4 h-4 text-gray-300 shrink-0 mt-1" />
                    </div>
                  );
                  if (item.href) {
                    return (
                      <li key={item.id}>
                        <a
                          href={item.href}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="block focus:outline-none focus:ring-2 focus:ring-accent/40 rounded-lg"
                        >
                          {body}
                        </a>
                      </li>
                    );
                  }
                  return (
                    <li key={item.id}>
                      <Link
                        to={item.to!}
                        className="block focus:outline-none focus:ring-2 focus:ring-accent/40 rounded-lg"
                      >
                        {body}
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
