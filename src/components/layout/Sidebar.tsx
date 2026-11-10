import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useLocation } from 'react-router-dom';
import {
  Settings,
  Home,
  List,
  CheckCircle2,
  GraduationCap,
  Calendar,
  ChevronDown,
  ChevronsLeft,
  ChevronsRight,
  FileText,
  MessageSquare,
  BarChart,
  Bell,
  Search,
  Sparkles,
  WandSparkles,
  ExternalLink,
  ShieldCheck,
  ShieldAlert,
  Tags,
  Library,
  MessageSquarePlus,
  Eraser,
  Wrench,
  ScrollText,
  FlaskConical,
  BookOpen,
  KeyRound,
} from 'lucide-react';
import { cn } from '../../lib/utils';
import { renderNavIcon } from '../../lib/navIcons';
import { DatabricksIcon, GenieIcon } from '../icons/DatabricksIcons';
import { CURRENT_VERSION } from '../../lib/releaseNotes';
import { FeedbackModal } from '../feedback/FeedbackModal';

import { useBrandingStore } from '../../stores/brandingStore';
import { hasCapability, matchesRoles, useUserStore } from '../../stores/userStore';
import { useRequestStore } from '../../stores/requestStore';
import { api } from '../../services/api';
import { getWhoAmI } from '../../services/metadataApi';
import { ROLE_PRIORITY } from '../../types';
import type { UserPersona } from '../../types';

// Explicit render order for sidebar groups. Groups not listed here fall to
// the end (preserving their relative insertion order). Driving rendering
// from this constant — rather than from the order items happen to appear
// in `navItems` — keeps the layout stable as dynamic items (embedded apps,
// Genie One) get spliced in based on branding config.
const GROUP_ORDER = [
  'Discover & Request',
  'Data & AI',
  'Learn & Share',
  'Watch Tower',
  'Control Tower',
] as const;

// Embedded apps saved under a group name the sidebar no longer has.
const RENAMED_GROUPS: Record<string, string> = {
  'Discover & Analyze': 'Discover & Request',
  'Build & Customize': 'Data & AI',
};

// Per-group collapse state is persisted to localStorage so the sidebar
// remembers each section's expanded/collapsed state across page reloads.
// The map shape is `{ [groupId: string]: true /* collapsed */ }`.
//
// Nothing is collapsed by default: every section starts EXPANDED so the
// full navigation is always discoverable. Collapsing is entirely opt-in —
// once the user collapses a section, that choice is written to localStorage
// and restored on subsequent loads.
const GROUP_COLLAPSE_STORAGE_KEY = 'sidebar-collapsed-groups';

function loadCollapsedGroups(): Record<string, boolean> {
  if (typeof window === 'undefined') return {};
  try {
    const raw = window.localStorage.getItem(GROUP_COLLAPSE_STORAGE_KEY);
    return raw ? (JSON.parse(raw) as Record<string, boolean>) : {};
  } catch {
    return {};
  }
}

function saveCollapsedGroups(state: Record<string, boolean>): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(
      GROUP_COLLAPSE_STORAGE_KEY,
      JSON.stringify(state)
    );
  } catch {
    /* storage disabled or full — non-fatal */
  }
}

interface NavItem {
  id: string;
  title: string;
  icon: React.ReactNode;
  group: string;
  // What the page is for, shown on hover or keyboard focus.
  tooltip: string;
  // Capability id(s) from GET /roles/me; holding any one shows the item. If
  // undefined (and no gate), shown to everyone.
  capability?: string | string[];
  // Embedded apps only: the roles (by name) allowed to see the app.
  allowedPersonas?: string[];
  // Shown by a backend permission check instead (e.g. Access Rules, which
  // approver-group members see without holding an admin role).
  gate?: 'access_rules' | 'metadata' | 'requests';
  // Exactly one of `path` (internal route) or `href` (external URL) should
  // be set. External items render as `<a target="_blank">` and pick up a
  // small external-link glyph next to the label when the rail is expanded.
  path?: string;
  href?: string;
}

const navItems: NavItem[] = [
  // Discover & Request — the unified chat agent leads this section. Its title
  // is overridden at render time with "<brand short name> Agent" (see allNavItems).
  {
    id: 'home', title: 'Agent', icon: <WandSparkles className="w-5 h-5" />, path: '/request', group: 'Discover & Request',
    tooltip: 'Chat with the assistant to find data, check your access and make requests.',
  },
  {
    id: 'data_discovery', title: 'Discover', icon: <Search className="w-5 h-5" />, path: '/discovery', group: 'Discover & Request',
    tooltip: 'Browse and search the data catalog: business domains, tables, metric views, dashboards and lineage.',
  },
  // One page with tabs; opens on "Needs my approval" while anything is waiting.
  {
    id: 'requests', title: 'My Requests & Approvals', icon: <List className="w-5 h-5" />, path: '/requests',
    group: 'Discover & Request', gate: 'requests',
    tooltip: 'Follow the requests you have made, and approve or reject the ones waiting on you.',
  },
  {
    id: 'reports', title: 'Reports', icon: <BarChart className="w-5 h-5" />, path: '/reports', group: 'Discover & Request',
    tooltip: 'Set up and manage reports the agent writes on a schedule.',
  },

  // Community - Available to everyone
  {
    id: 'training', title: 'Training', icon: <GraduationCap className="w-5 h-5" />, path: '/community/training', group: 'Learn & Share',
    tooltip: 'Follow learning paths and courses on the platform.',
  },
  {
    id: 'event_calendar', title: 'Event Calendar', icon: <Calendar className="w-5 h-5" />, path: '/community/events', group: 'Learn & Share',
    tooltip: 'Upcoming events and sessions.',
  },
  {
    id: 'templates_assets', title: 'Templates & Assets', icon: <FileText className="w-5 h-5" />, path: '/community/assets', group: 'Learn & Share',
    tooltip: 'Find and share design patterns, templates and reusable components.',
  },
  {
    id: 'community_links', title: 'Community Links', icon: <MessageSquare className="w-5 h-5" />, path: '/community/links', group: 'Learn & Share',
    tooltip: 'Curated links to resources, tools and documentation.',
  },

  // Governance - Restricted
  {
    id: 'certification',
    title: 'Data Certification (ODCS)',
    icon: <CheckCircle2 className="w-5 h-5" />,
    path: '/governance/certification',
    group: 'Watch Tower',
    capability: 'governance.control_tower',
    tooltip: 'See which datasets meet your standards: data contracts, quality results and certification status.',
  },
  {
    id: 'odps',
    title: 'Data Products (ODPS)',
    icon: <FileText className="w-5 h-5" />,
    path: '/governance/odps',
    group: 'Watch Tower',
    capability: 'governance.control_tower',
    tooltip: 'Catalog data products and draft new ones from certified data contracts.',
  },
  {
    id: 'allowlist',
    title: 'Allowlist',
    icon: <ShieldCheck className="w-5 h-5" />,
    path: '/governance/allowlist',
    group: 'Watch Tower',
    capability: 'allowlist.manage',
    tooltip: 'Record approved exceptions that OmniGuard leaves alone.',
  },
  {
    id: 'sentinel',
    title: 'OmniGuard',
    icon: <ShieldAlert className="w-5 h-5" />,
    path: '/governance/sentinel',
    group: 'Watch Tower',
    capability: 'sentinel.view',
    tooltip: 'Scan your target workspaces for policy violations and review what to fix.',
  },
  {
    id: 'tag_management',
    title: 'Metadata Manager',
    icon: <Tags className="w-5 h-5" />,
    path: '/governance/metadata',
    group: 'Watch Tower',
    gate: 'metadata',
    tooltip: 'Find a table, then change its description and tags.',
  },
  {
    id: 'access_rules',
    title: 'Access Rules',
    icon: <KeyRound className="w-5 h-5" />,
    path: '/governance/access-rules',
    group: 'Watch Tower',
    gate: 'access_rules',
    tooltip: 'Which groups hold which privileges on data and workspaces, exported for Terraform.',
  },
  // Admin - Restricted
  {
    id: 'admin',
    title: 'Admin',
    icon: <Settings className="w-5 h-5" />,
    path: '/admin/dashboard',
    group: 'Control Tower',
    capability: ['admin.hub', 'feedback.manage'],
    tooltip: 'Settings, roles, feedback and system messages.',
  },
  {
    id: 'context_catalog',
    title: 'Context Catalog',
    icon: <Library className="w-5 h-5" />,
    path: '/governance/context-catalog',
    group: 'Control Tower',
    capability: 'context_catalog.manage',
    tooltip: 'The curated knowledge base the assistant draws its answers from.',
  },
  {
    id: 'workflows',
    title: 'Workflow Studio',
    icon: <Sparkles className="w-5 h-5" />,
    path: '/build/workflows',
    group: 'Control Tower',
    capability: 'workflows.author',
    tooltip: 'Author the workflows the assistant runs, with their tools, policies and approvals.',
  },
  {
    id: 'tool_registry',
    title: 'Tool Registry',
    icon: <Wrench className="w-5 h-5" />,
    path: '/build/tool-registry',
    group: 'Control Tower',
    capability: 'tools.registry.view',
    tooltip: 'Every tool the assistant can use: where it is available, who can use it and which identity it runs as.',
  },
  {
    id: 'preview_features',
    title: 'Preview Features',
    icon: <FlaskConical className="w-5 h-5" />,
    path: '/build/preview-features',
    group: 'Control Tower',
    capability: 'preview_features.manage',
    tooltip: 'Databricks Beta and Public Preview features across your target workspaces, and requests to turn them on.',
  },
  {
    id: 'training_admin',
    title: 'Training Studio',
    icon: <GraduationCap className="w-5 h-5" />,
    path: '/build/training',
    group: 'Control Tower',
    capability: 'training.manage',
    tooltip: 'Author learning tracks and courses, upload media and track who completes them.',
  },
];

interface SidebarProps {
  collapsed: boolean;
  onToggle: () => void;
}

// Personas that show up in the dev-mode role override list. Kept in
// sync with the legacy Header component so behavior is identical.
const DEV_MODE_PERSONAS: { label: string; value: UserPersona }[] = ROLE_PRIORITY.map((role) => ({
  label: role,
  value: role,
}));

export function Sidebar({ collapsed, onToggle }: SidebarProps) {
  const location = useLocation();

  // Reactive store selectors
  const currentPersona = useUserStore((state) => state.currentPersona);
  const currentUser = useUserStore((state) => state.currentUser);
  const isDevMode = useUserStore((state) => state.isDevMode);
  const activeRoleOverride = useUserStore((state) => state.activeRoleOverride);
  const toggleDevMode = useUserStore((state) => state.toggleDevMode);
  const disableDevMode = useUserStore((state) => state.disableDevMode);
  const setRoleOverride = useUserStore((state) => state.setRoleOverride);

  const pendingCount = useRequestStore((state) =>
    state.getPendingApprovalsCount()
  );

  const { uiTabs } = useBrandingStore();
  const brandName = useBrandingStore((s) => s.brandName);
  const brandLogoUrl = useBrandingStore((s) => s.brandLogoUrl);
  const brandShortName = useBrandingStore((s) => s.brandShortName);
  const genieOneUrl = useBrandingStore((s) => s.genieOneUrl);
  const embeddedApps = useBrandingStore((s) => s.embeddedApps);
  // "Dev Persona Mode" is a local/dev-only convenience; the backend ignores the
  // role-override header outside dev envs, so hide the toggle everywhere else.
  const devFeaturesEnabled = useBrandingStore((s) => s.devFeaturesEnabled);
  const brandingLoaded = useBrandingStore((s) => s.hasLoaded);

  // Permission checks are tagged with the role they were made for, so a stale
  // answer is ignored while the check for a newly picked role is in flight.
  const overrideRole = activeRoleOverride ?? '';

  const accessRulesTabOn = uiTabs?.access_rules !== false;
  const [accessRulesView, setAccessRulesView] = useState<{ role: string; ok: boolean } | null>(null);
  useEffect(() => {
    if (!accessRulesTabOn || !currentUser) return;
    let cancelled = false;
    api
      .getRbacPermissions()
      .then((p) => {
        if (!cancelled) setAccessRulesView({ role: overrideRole, ok: p.can_view });
      })
      .catch(() => {
        if (!cancelled) setAccessRulesView({ role: overrideRole, ok: false });
      });
    return () => {
      cancelled = true;
    };
  }, [accessRulesTabOn, currentUser, overrideRole]);
  const canViewAccessRules =
    accessRulesTabOn && !!currentUser && accessRulesView?.role === overrideRole && accessRulesView.ok;

  // Data owners edit metadata without an admin role, so the link follows the
  // backend's browse scope (governance, or owners of at least one object)
  // rather than the persona; Ops Admins get it for sync history alone.
  const metadataTabOn = uiTabs?.tag_management !== false;
  const [metadataBrowse, setMetadataBrowse] = useState<{ role: string; ok: boolean } | null>(null);
  useEffect(() => {
    if (!metadataTabOn || !currentUser) return;
    let cancelled = false;
    getWhoAmI()
      .then((w) => {
        if (!cancelled) setMetadataBrowse({ role: overrideRole, ok: w.can_browse || !!w.can_view_runs });
      })
      .catch(() => {
        if (!cancelled) setMetadataBrowse({ role: overrideRole, ok: false });
      });
    return () => {
      cancelled = true;
    };
  }, [metadataTabOn, currentUser, overrideRole]);
  const canBrowseMetadata =
    metadataTabOn && !!currentUser && metadataBrowse?.role === overrideRole && metadataBrowse.ok;

  // Once branding confirms this is a non-dev environment, clear any stale
  // dev-mode override persisted in localStorage (e.g. carried over from a lower
  // env) so the client stops sending the now-ignored role-override header and
  // the displayed persona reverts to the user's real roles.
  useEffect(() => {
    if (brandingLoaded && !devFeaturesEnabled) {
      void disableDevMode();
    }
  }, [brandingLoaded, devFeaturesEnabled, disableDevMode]);

  // User menu (account dropdown) is click-to-toggle for accessibility.
  // Close on outside click and on Escape.
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  // Close the user menu when the rail collapses or expands so it doesn't
  // dangle in a stale position relative to its trigger.
  const [menuRailCollapsed, setMenuRailCollapsed] = useState(collapsed);
  if (menuRailCollapsed !== collapsed) {
    setMenuRailCollapsed(collapsed);
    setUserMenuOpen(false);
  }
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const userMenuRef = useRef<HTMLDivElement>(null);

  // Wipe the user's chat history and cached personalization. Transcripts are now
  // stored server-side (so they follow the user across devices), so this deletes
  // them there as well as in this browser — otherwise clearing on a shared
  // machine would leave the history to reappear on the next load.
  const handleClearMyData = useCallback(async () => {
    const confirmed = window.confirm(
      'Clear your chat history?\n\n' +
        'This deletes your saved conversations and personalized suggestions, on ' +
        'this device and on every device you use. Requests and approvals you have ' +
        'submitted are not affected.',
    );
    if (!confirmed) return;
    try {
      await api.deleteChatSessions();
    } catch {
      // Still clear locally: a failed server delete shouldn't leave the user
      // staring at history they just asked to remove.
    }
    try {
      const prefixes = ['chatview_messages_', 'home_suggestions_'];
      for (const store of [window.localStorage, window.sessionStorage]) {
        const keys: string[] = [];
        for (let i = 0; i < store.length; i++) {
          const k = store.key(i);
          if (k && prefixes.some((p) => k.startsWith(p))) keys.push(k);
        }
        keys.forEach((k) => store.removeItem(k));
      }
    } catch {
      /* storage disabled — nothing to clear */
    }
    window.location.reload();
  }, []);

  useEffect(() => {
    if (!userMenuOpen) return;
    const handleClick = (event: MouseEvent) => {
      if (
        userMenuRef.current &&
        !userMenuRef.current.contains(event.target as Node)
      ) {
        setUserMenuOpen(false);
      }
    };
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setUserMenuOpen(false);
    };
    document.addEventListener('mousedown', handleClick);
    document.addEventListener('keydown', handleKey);
    return () => {
      document.removeEventListener('mousedown', handleClick);
      document.removeEventListener('keydown', handleKey);
    };
  }, [userMenuOpen]);

  const userInitials =
    currentUser?.full_name
      ?.split(' ')
      .map((part) => part[0])
      .filter(Boolean)
      .slice(0, 2)
      .join('') || currentUser?.email?.[0]?.toUpperCase() || '?';
  const userDisplayName = currentUser?.full_name || currentUser?.email || 'Signed-in user';
  const userEmail = currentUser?.email || '';

  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>(
    loadCollapsedGroups
  );

  // Rendered in a portal: the nav scrolls and clips horizontally, so a tooltip
  // positioned inside it would be cut off at the rail's edge.
  const [navTip, setNavTip] = useState<{ id: string; title: string; text: string; top: number; left: number } | null>(null);
  const showNavTip = useCallback((id: string, title: string, text: string, el: HTMLElement) => {
    const rect = el.getBoundingClientRect();
    setNavTip({ id, title, text, top: rect.top + rect.height / 2, left: rect.right + 8 });
  }, []);
  const hideNavTip = useCallback(() => setNavTip(null), []);

  const toggleGroup = useCallback((groupId: string) => {
    setCollapsedGroups((prev) => {
      const next = { ...prev, [groupId]: !prev[groupId] };
      saveCollapsedGroups(next);
      return next;
    });
  }, []);

  // Data & AI: config-driven embedded apps in list order, then the account's
  // Genie One (only once an admin sets its address) and the Workspaces page.
  // Each embedded app renders in-app at /embedded/<id> so it participates in
  // normal active-state styling and back/forward history.
  const externalNavItems = React.useMemo<NavItem[]>(() => {
    const items: NavItem[] = embeddedApps.map((app) => {
      const group = app.group || 'Data & AI';
      return {
        id: app.id,
        title: app.title,
        icon: renderNavIcon(app.icon),
        path: `/embedded/${app.id}`,
        group: RENAMED_GROUPS[group] ?? group,
        tooltip: app.description || `Open ${app.title} here.`,
        ...(app.allowedPersonas ? { allowedPersonas: app.allowedPersonas } : {}),
      };
    });
    if (genieOneUrl) {
      items.push({
        id: 'genie_one',
        title: 'Genie One',
        icon: <GenieIcon className="w-5 h-5" />,
        href: genieOneUrl,
        group: 'Data & AI',
        tooltip: 'Ask questions of your data in plain language, across every workspace, and get answers, charts and dashboards.',
      });
    }
    items.push({
      id: 'workspaces',
      title: 'Workspaces',
      icon: <DatabricksIcon className="w-5 h-5" />,
      path: '/workspaces',
      group: 'Data & AI',
      tooltip: 'The Databricks workspaces you can use, each with links to its lakehouse and its Genie One.',
    });
    return items;
  }, [embeddedApps, genieOneUrl]);

  const allNavItems = React.useMemo(
    () => {
      const items = navItems.map((item) =>
        item.id === 'home' ? { ...item, title: `${brandShortName} Agent` } : item
      );
      return [...items, ...externalNavItems];
    },
    [externalNavItems, brandShortName]
  );

  // Filter items based on current persona
  const filteredItems = allNavItems.filter(item => {
    // Check if explicitly disabled in config (if not present or undefined, default to true)
    if (uiTabs && uiTabs[item.id] === false) {
      return false;
    }

    if (item.gate === 'access_rules') return canViewAccessRules;
    if (item.gate === 'metadata') return canBrowseMetadata;
    if (item.gate === 'requests') {
      return uiTabs?.my_requests !== false || uiTabs?.pending_approvals !== false;
    }
    if (item.allowedPersonas && !matchesRoles(currentUser, item.allowedPersonas)) return false;
    if (!item.capability) return true;
    return hasCapability(currentUser, ...(Array.isArray(item.capability) ? item.capability : [item.capability]));
  });

  const groupedItems = filteredItems.reduce((acc, item) => {
    if (!acc[item.group]) {
      acc[item.group] = [];
    }
    acc[item.group].push(item);
    return acc;
  }, {} as Record<string, NavItem[]>);

  const orderedGroups = Object.entries(groupedItems).sort(([a], [b]) => {
    const ai = (GROUP_ORDER as readonly string[]).indexOf(a);
    const bi = (GROUP_ORDER as readonly string[]).indexOf(b);
    return (
      (ai === -1 ? Number.MAX_SAFE_INTEGER : ai) -
      (bi === -1 ? Number.MAX_SAFE_INTEGER : bi)
    );
  });

  return (
    <>
    <div
      className={cn(
        "bg-nav-bg text-nav-text border-r border-nav-border transition-all duration-300 flex flex-col relative",
        collapsed ? "w-16" : "w-72"
      )}
    >
      {/* ─── Brand + collapse toggle ──────────────────────────────── */}
      <div
        className={cn(
          "shrink-0 border-b border-nav-border",
          collapsed ? "px-2 py-3" : "px-4 py-3"
        )}
      >
        {collapsed ? (
          <div className="flex flex-col items-center gap-2">
            {/* In collapsed mode the actual brand wordmark is too small
                to be legible, so we substitute a generic "home" glyph
                that still clearly answers "click here to go home" while
                reading cleanly inside the 40px chip. */}
            <Link
              to="/"
              aria-label={`${brandName} home`}
              title={brandName}
              className="w-10 h-10 flex items-center justify-center rounded-lg bg-white/10 text-white hover:bg-nav-hover transition-colors"
            >
              <Home className="w-5 h-5" />
            </Link>
            <button
              type="button"
              onClick={onToggle}
              aria-label="Expand navigation"
              aria-expanded={false}
              title="Expand navigation"
              className="w-10 h-10 flex items-center justify-center rounded-lg bg-white/10 text-white ring-1 ring-white/20 hover:bg-nav-hover hover:ring-white/40 transition-colors"
            >
              <ChevronsRight className="w-7 h-7" />
            </button>
          </div>
        ) : (
          // Stacked layout: a wide wordmark logo plus
          // a multi-word brand name (e.g. "Enterprise Data Hub") won't
          // fit on one line inside the 288px sidebar without truncating
          // one or the other. Put the logo + toggle on the top row and
          // drop the brand name onto its own line below.
          <div className="flex items-start gap-2">
            <Link
              to="/"
              aria-label={brandName}
              className="flex flex-col gap-1.5 min-w-0 flex-1 rounded-md p-1 -m-1 hover:bg-nav-hover transition-colors"
            >
              {brandLogoUrl && (
                <img
                  src={brandLogoUrl}
                  alt=""
                  className="h-8 w-auto max-w-[180px] object-contain"
                />
              )}
              <span className={cn(
                "font-semibold text-white truncate leading-tight",
                // With no logo the name is the only branding, so give it more
                // presence; when a logo sits above it, keep the name compact.
                brandLogoUrl ? "text-sm" : "text-xl"
              )}>
                {brandName}
              </span>
            </Link>
            <button
              type="button"
              onClick={onToggle}
              aria-label="Collapse navigation"
              aria-expanded={true}
              title="Collapse navigation"
              className="w-9 h-9 mt-0.5 flex items-center justify-center rounded-lg bg-white/10 text-white ring-1 ring-white/20 hover:bg-nav-hover hover:ring-white/40 transition-colors shrink-0"
            >
              <ChevronsLeft className="w-6 h-6" />
            </button>
          </div>
        )}
      </div>

      {/* ─── Primary navigation ──────────────────────────────────── */}
      <nav className={cn(
        // overflow-x-hidden guarantees the collapsed icon rail can never
        // grow a horizontal scrollbar even if the OS shows space-taking
        // scrollbars (macOS "Always show" mode). dark-scrollbar themes
        // the vertical bar so it sits on top of the navy surface cleanly.
        "flex-1 overflow-y-auto overflow-x-hidden space-y-6 dark-scrollbar",
        collapsed ? "px-2 py-4" : "p-4"
      )} onScroll={hideNavTip}>
        {orderedGroups.map(([group, items]) => {
          // When the entire rail is collapsed (icons-only mode) per-group
          // collapse is irrelevant — we always show every icon.
          const isGroupCollapsed = !collapsed && !!collapsedGroups[group];
          const groupSlug = group.replace(/\s+/g, '-').toLowerCase();
          return (
          <div key={group}>
            {!collapsed && (
              <button
                type="button"
                onClick={() => toggleGroup(group)}
                aria-expanded={!isGroupCollapsed}
                aria-controls={`sidebar-group-${groupSlug}`}
                className="group w-full flex items-center justify-between mb-2 px-1 py-1 -mx-1 -my-1 rounded text-nav-text-muted hover:text-white transition-colors"
              >
                <h3 className="text-xs font-semibold uppercase tracking-wider">
                  {group}
                </h3>
                <ChevronDown
                  className={cn(
                    "w-5 h-5 transition-transform duration-200 text-white group-hover:text-white",
                    isGroupCollapsed && "-rotate-90"
                  )}
                  aria-hidden="true"
                />
              </button>
            )}
            <div
              id={`sidebar-group-${groupSlug}`}
              className={cn("space-y-1", isGroupCollapsed && "hidden")}
            >
              {items.map((item) => {
                const isExternal = !!item.href;
                const isActive = isExternal
                  ? false
                  : item.path === '/admin/dashboard'
                    ? location.pathname.startsWith('/admin')
                    : item.id === 'home'
                      // The agent landing renders at both '/' and '/request'.
                      ? location.pathname === '/' || location.pathname === '/request'
                    : item.gate === 'requests'
                      ? location.pathname.startsWith('/requests') || location.pathname === '/approvals'
                      : location.pathname === item.path;
                const itemPath = item.gate === 'requests' && (pendingCount > 0 || uiTabs?.my_requests === false)
                  ? '/approvals'
                  : item.path;
                const badgeCount = item.gate === 'requests' ? pendingCount : 0;
                // Icons in `navItems` are pre-built with `w-5 h-5`. In the
                // collapsed rail they look puny, so re-clone them at a
                // larger size so they read clearly.
                const renderedIcon = React.isValidElement(item.icon) && collapsed
                  ? React.cloneElement(item.icon as React.ReactElement<{ className?: string }>, {
                      className: 'w-6 h-6',
                    })
                  : item.icon;
                const itemKey = item.id;
                const className = cn(
                  "flex items-center text-sm transition-colors",
                  collapsed
                    ? cn(
                        // 40x40 chip leaves room for a 6px scrollbar in the
                        // 64px-wide collapsed rail without ever being clipped
                        // or pushing horizontal overflow.
                        "justify-center w-10 h-10 mx-auto rounded-lg",
                        isActive
                          ? "bg-nav-active text-nav-active-text shadow-sm"
                          : "bg-white/5 text-gray-200 hover:bg-nav-hover hover:text-white"
                      )
                    : cn(
                        "gap-3 px-3 py-2 rounded-md",
                        isActive
                          ? "bg-nav-active text-nav-active-text"
                          : "text-gray-300 hover:bg-nav-hover hover:text-white"
                      )
                );
                const tipNotes = [
                  ...(badgeCount > 0 ? [`${badgeCount} waiting for you.`] : []),
                  ...(isExternal ? ['Opens in a new tab.'] : []),
                ];
                const tipText = [item.tooltip, ...tipNotes].join(' ');
                const tipProps = {
                  'aria-label': collapsed ? item.title : undefined,
                  'aria-describedby': navTip?.id === itemKey ? 'sidebar-nav-tip' : undefined,
                  onMouseEnter: (e: React.MouseEvent<HTMLElement>) => showNavTip(itemKey, item.title, tipText, e.currentTarget),
                  onFocus: (e: React.FocusEvent<HTMLElement>) => showNavTip(itemKey, item.title, tipText, e.currentTarget),
                  onMouseLeave: hideNavTip,
                  onBlur: hideNavTip,
                  onClick: hideNavTip,
                };
                const body = (
                  <>
                    {collapsed && badgeCount > 0 ? (
                      <span className="relative inline-flex">
                        {renderedIcon}
                        <span className="absolute -top-1.5 -right-1.5 min-w-[16px] h-4 px-1 bg-red-500 text-white text-[10px] font-bold rounded-full flex items-center justify-center leading-none">
                          {badgeCount > 9 ? '9+' : badgeCount}
                        </span>
                      </span>
                    ) : renderedIcon}
                    {!collapsed && (
                      <span className="flex-1 flex items-center gap-1.5">
                        <span>{item.title}</span>
                        {isExternal && (
                          <ExternalLink className="w-3 h-3 text-nav-text-muted shrink-0" />
                        )}
                        {badgeCount > 0 && (
                          <span
                            className="ml-auto min-w-[20px] h-5 px-1.5 bg-red-500 text-white text-[11px] font-bold rounded-full flex items-center justify-center leading-none"
                            aria-label={`${badgeCount} pending`}
                          >
                            {badgeCount > 99 ? '99+' : badgeCount}
                          </span>
                        )}
                      </span>
                    )}
                  </>
                );

                if (isExternal) {
                  return (
                    <a
                      key={itemKey}
                      href={item.href}
                      target="_blank"
                      rel="noopener noreferrer"
                      className={className}
                      {...tipProps}
                    >
                      {body}
                    </a>
                  );
                }
                return (
                  <Link
                    key={itemKey}
                    to={itemPath!}
                    className={className}
                    {...tipProps}
                  >
                    {body}
                  </Link>
                );
              })}
            </div>
          </div>
          );
        })}
      </nav>

      {/* ─── Bottom-aligned footer: user menu (holds notifications) ── */}
      <div
        className={cn(
          "shrink-0 border-t border-nav-border",
          collapsed ? "p-2 space-y-1" : "p-3 space-y-1"
        )}
      >
        <div ref={userMenuRef} className="relative">
          <button
            type="button"
            onClick={() => setUserMenuOpen((prev) => !prev)}
            aria-haspopup="menu"
            aria-expanded={userMenuOpen}
            aria-label={
              pendingCount > 0
                ? `Account menu for ${userDisplayName} (${pendingCount} pending)`
                : `Account menu for ${userDisplayName}`
            }
            title={
              collapsed
                ? pendingCount > 0
                  ? `${userDisplayName} (${pendingCount} pending)`
                  : userDisplayName
                : undefined
            }
            className={cn(
              "w-full flex items-center text-sm transition-colors text-gray-200 hover:bg-nav-hover hover:text-white",
              collapsed
                ? "justify-center p-1 rounded-lg"
                : "gap-3 px-2 py-2 rounded-md"
            )}
          >
            <span className="relative w-8 h-8 shrink-0 rounded-full bg-accent text-white flex items-center justify-center text-xs font-semibold shadow-sm">
              {userInitials}
              {pendingCount > 0 && (
                <span
                  aria-hidden="true"
                  className="absolute -top-1.5 -right-1.5 min-w-[16px] h-4 px-1 bg-red-500 text-white text-[10px] font-bold rounded-full flex items-center justify-center leading-none ring-2 ring-nav-bg"
                >
                  {pendingCount > 9 ? '9+' : pendingCount}
                </span>
              )}
            </span>
            {!collapsed && (
              <>
                <span className="flex-1 flex flex-col items-start min-w-0">
                  <span className="text-sm font-semibold text-white truncate leading-tight max-w-[160px]">
                    {userDisplayName}
                  </span>
                  <span className="text-[10px] uppercase tracking-tight font-bold text-nav-text-muted">
                    {currentPersona}
                  </span>
                </span>
                <ChevronDown
                  className={cn(
                    "w-4 h-4 text-nav-text-muted transition-transform shrink-0",
                    userMenuOpen && "rotate-180"
                  )}
                  aria-hidden="true"
                />
              </>
            )}
          </button>

          {userMenuOpen && (
            // Anchored above the trigger and aligned to its left edge by
            // default; in the collapsed rail it pops out to the right
            // instead so the 256px menu doesn't get clipped inside the
            // 64px-wide sidebar. Layout's row uses min-h-0 (not
            // overflow-hidden) so this popout is not clipped.
            <div
              role="menu"
              className={cn(
                "absolute z-50 w-64 bg-white rounded-lg shadow-xl border border-gray-200 py-2 text-gray-800 animate-in fade-in slide-in-from-bottom-2",
                collapsed
                  ? "bottom-0 left-full ml-2"
                  : "bottom-full left-0 mb-2"
              )}
            >
              <div className="px-4 py-3 border-b border-gray-100">
                <p className="text-xs font-semibold text-gray-400 uppercase">Account</p>
                <p className="text-sm font-medium text-gray-900 truncate mt-1">
                  {userEmail}
                </p>
              </div>

              {devFeaturesEnabled && (
              <div className="px-4 py-3 border-b border-gray-100 bg-gray-50/50">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Settings className="w-3.5 h-3.5 text-gray-400" />
                    <span className="text-xs font-bold text-gray-700">
                      Dev Persona Mode
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={(event) => {
                      event.stopPropagation();
                      toggleDevMode();
                    }}
                    className={cn(
                      "w-8 h-4 rounded-full p-0.5 transition-colors focus:outline-none",
                      isDevMode ? "bg-blue-600" : "bg-gray-300"
                    )}
                    aria-pressed={isDevMode}
                    aria-label="Toggle dev persona mode"
                  >
                    <div
                      className={cn(
                        "w-3 h-3 bg-white rounded-full shadow-sm transition-transform duration-200",
                        isDevMode ? "translate-x-4" : "translate-x-0"
                      )}
                    />
                  </button>
                </div>
                {isDevMode && (
                  <div className="mt-3 space-y-2 animate-in fade-in slide-in-from-top-2">
                    <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider">
                      Simulate Role
                    </p>
                    <div className="grid grid-cols-1 gap-1">
                      <button
                        type="button"
                        onClick={() => setRoleOverride(null)}
                        className={cn(
                          "text-left px-2 py-1.5 text-xs rounded-md transition-colors",
                          !activeRoleOverride
                            ? "bg-blue-100 text-blue-700 font-bold"
                            : "hover:bg-white text-gray-600 shadow-sm border border-gray-100"
                        )}
                      >
                        Default (My Real Roles)
                      </button>
                      {DEV_MODE_PERSONAS.map((p) => (
                        <button
                          key={p.value}
                          type="button"
                          onClick={() => setRoleOverride(p.value)}
                          className={cn(
                            "text-left px-2 py-1.5 text-xs rounded-md transition-colors",
                            activeRoleOverride === p.value
                              ? "bg-blue-100 text-blue-700 font-bold"
                              : "hover:bg-white text-gray-600 shadow-sm border border-gray-100"
                          )}
                        >
                          {p.label}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
              )}

              <div className="px-2 pt-2">
                <Link
                  to="/approvals"
                  onClick={() => setUserMenuOpen(false)}
                  className="w-full flex items-center gap-2 text-left px-3 py-2 text-sm text-gray-600 hover:bg-gray-50 rounded-md transition-colors font-medium"
                >
                  <Bell className="w-4 h-4" />
                  <span className="flex-1">Notifications</span>
                  {pendingCount > 0 && (
                    <span className="min-w-[20px] h-5 px-1.5 bg-red-500 text-white text-[11px] font-bold rounded-full flex items-center justify-center leading-none">
                      {pendingCount > 99 ? '99+' : pendingCount}
                    </span>
                  )}
                </Link>
                {uiTabs?.guide !== false && (
                  <Link
                    to="/guide"
                    onClick={() => setUserMenuOpen(false)}
                    className="w-full flex items-center gap-2 text-left px-3 py-2 text-sm text-gray-600 hover:bg-gray-50 rounded-md transition-colors font-medium"
                  >
                    <BookOpen className="w-4 h-4" />
                    Guide
                  </Link>
                )}
                <Link
                  to="/release-notes"
                  onClick={() => setUserMenuOpen(false)}
                  className="w-full flex items-center gap-2 text-left px-3 py-2 text-sm text-gray-600 hover:bg-gray-50 rounded-md transition-colors font-medium"
                >
                  <ScrollText className="w-4 h-4" />
                  <span className="flex-1">Release notes</span>
                  {CURRENT_VERSION && (
                    <span className="text-xs font-normal text-gray-400">v{CURRENT_VERSION}</span>
                  )}
                </Link>
                {uiTabs?.feedback !== false && (
                  <button
                    type="button"
                    onClick={() => {
                      setUserMenuOpen(false);
                      setFeedbackOpen(true);
                    }}
                    className="w-full flex items-center gap-2 text-left px-3 py-2 text-sm text-gray-600 hover:bg-gray-50 rounded-md transition-colors font-medium"
                  >
                    <MessageSquarePlus className="w-4 h-4" />
                    Send feedback
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => {
                    setUserMenuOpen(false);
                    void handleClearMyData();
                  }}
                  className="w-full flex items-center gap-2 text-left px-3 py-2 text-sm text-gray-600 hover:bg-gray-50 rounded-md transition-colors font-medium"
                >
                  <Eraser className="w-4 h-4" />
                  Clear my data
                </button>
                {/* No app-managed session to end: auth is Databricks Apps
                    header-based (OBO), so sign-out is owned by the workspace,
                    not this app. A button here would be a misleading no-op. */}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
    {navTip && createPortal(
      <div
        id="sidebar-nav-tip"
        role="tooltip"
        className="pointer-events-none fixed z-[60] w-64 -translate-y-1/2 rounded-md bg-gray-900 px-3 py-2 text-xs leading-snug text-white shadow-lg ring-1 ring-white/10"
        style={{ top: navTip.top, left: navTip.left }}
      >
        <p className="font-semibold">{navTip.title}</p>
        <p className="mt-0.5 text-gray-200">{navTip.text}</p>
      </div>,
      document.body,
    )}
    <FeedbackModal open={feedbackOpen} onClose={() => setFeedbackOpen(false)} />
    </>
  );
}
