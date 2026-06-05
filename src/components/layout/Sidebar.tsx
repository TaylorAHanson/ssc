import React, { useCallback, useEffect, useRef, useState } from 'react';
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
  Upload,
  Search,
  Sparkles,
  WandSparkles,
  Database,
  LayoutDashboard,
  ExternalLink,
  ShieldCheck,
  ShieldAlert,
  Tags,
  Library,
} from 'lucide-react';
import { cn } from '../../lib/utils';

import { useBrandingStore } from '../../stores/brandingStore';
import { useUserStore } from '../../stores/userStore';
import { useRequestStore } from '../../stores/requestStore';
import { genieHomeUrl, workspaceHomeUrl } from '../../lib/databricksLinks';
import type { UserPersona } from '../../types';

// Explicit render order for sidebar groups. Groups not listed here fall to
// the end (preserving their relative insertion order). Driving rendering
// from this constant — rather than from the order items happen to appear
// in `navItems` — keeps the layout stable as dynamic items (Genie,
// Command Center, Lakehouse) get spliced in based on branding config.
const GROUP_ORDER = [
  'Discover & Analyze',
  'Access & Provision',
  'Build & Customize',
  'Learn & Share',
  'Governance',
  'Admin',
] as const;

// Per-group collapse state is persisted to localStorage so the sidebar
// remembers each section's expanded/collapsed state across page reloads.
// The map shape is `{ [groupId: string]: true /* collapsed */ }`.
//
// First-load defaults below give "less essential" groups a collapsed
// initial state so the rail isn't visually overwhelming on a fresh
// visit. Once the user toggles a group, that choice is written to
// localStorage and overrides the default on subsequent loads.
const GROUP_COLLAPSE_STORAGE_KEY = 'sidebar-collapsed-groups';

const DEFAULT_COLLAPSED_GROUPS: Record<string, boolean> = {
  'Learn & Share': true,
  Governance: true,
  Admin: true,
};

function loadCollapsedGroups(): Record<string, boolean> {
  if (typeof window === 'undefined') return { ...DEFAULT_COLLAPSED_GROUPS };
  try {
    const raw = window.localStorage.getItem(GROUP_COLLAPSE_STORAGE_KEY);
    const stored = raw ? (JSON.parse(raw) as Record<string, boolean>) : {};
    // Stored entries override defaults per-group, so a user who has
    // explicitly expanded e.g. "Learn & Share" keeps it expanded across
    // reloads even though the default is collapsed.
    return { ...DEFAULT_COLLAPSED_GROUPS, ...stored };
  } catch {
    return { ...DEFAULT_COLLAPSED_GROUPS };
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
  allowedPersonas?: UserPersona[]; // If undefined, allowed for all
  // Exactly one of `path` (internal route) or `href` (external URL) should
  // be set. External items render as `<a target="_blank">` and pick up a
  // small external-link glyph next to the label when the rail is expanded.
  path?: string;
  href?: string;
}

// Derive a short label from the configured brand name (e.g. "Enterprise
// Data Hub" -> "EDH") so the agent entry reflects branding without
// hardcoding the app name. Single-word brands fall back to the word itself.
function brandAcronym(name: string): string {
  const words = (name || '').trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return '';
  if (words.length === 1) return words[0];
  return words.map((w) => w[0]!.toUpperCase()).join('');
}

const navItems: NavItem[] = [
  // Discover & Analyze — the unified chat agent leads this section. Its title
  // is overridden at render time with a brand-derived label (see allNavItems).
  { id: 'home', title: 'Agent', icon: <WandSparkles className="w-5 h-5" />, path: '/request', group: 'Discover & Analyze' },
  { id: 'data_discovery', title: 'View & Search Catalog', icon: <Search className="w-5 h-5" />, path: '/discovery', group: 'Discover & Analyze' },

  // Self Service
  { id: 'my_requests', title: 'My Requests', icon: <List className="w-5 h-5" />, path: '/requests', group: 'Access & Provision' },
  { id: 'pending_approvals', title: 'Pending Approvals', icon: <CheckCircle2 className="w-5 h-5" />, path: '/approvals', group: 'Access & Provision' },
  { id: 'reports', title: 'Reports', icon: <BarChart className="w-5 h-5" />, path: '/reports', group: 'Access & Provision' },

  // Community - Available to everyone
  { id: 'training', title: 'Training', icon: <GraduationCap className="w-5 h-5" />, path: '/community/training', group: 'Learn & Share' },
  { id: 'event_calendar', title: 'Event Calendar', icon: <Calendar className="w-5 h-5" />, path: '/community/events', group: 'Learn & Share' },
  { id: 'templates_assets', title: 'Templates & Assets', icon: <FileText className="w-5 h-5" />, path: '/community/assets', group: 'Learn & Share' },
  { id: 'community_links', title: 'Community Links', icon: <MessageSquare className="w-5 h-5" />, path: '/community/links', group: 'Learn & Share' },

  // Governance - Restricted
  {
    id: 'certification',
    title: 'Data Certification (ODCS)',
    icon: <CheckCircle2 className="w-5 h-5" />,
    path: '/governance/certification',
    group: 'Governance',
    allowedPersonas: ['Platform Admin', 'Governance Admin']
  },
  {
    id: 'odps',
    title: 'Data Products (ODPS)',
    icon: <FileText className="w-5 h-5" />,
    path: '/governance/odps',
    group: 'Governance',
    allowedPersonas: ['Platform Admin', 'Governance Admin']
  },
  {
    id: 'allowlist',
    title: 'Allowlist',
    icon: <ShieldCheck className="w-5 h-5" />,
    path: '/governance/allowlist',
    group: 'Governance',
    allowedPersonas: ['Platform Admin', 'Governance Admin']
  },
  {
    id: 'sentinel',
    title: 'Sentinel',
    icon: <ShieldAlert className="w-5 h-5" />,
    path: '/governance/sentinel',
    group: 'Governance',
    allowedPersonas: ['Platform Admin', 'Governance Admin']
  },
  {
    id: 'tag_management',
    title: 'Tag Management',
    icon: <Tags className="w-5 h-5" />,
    path: '/governance/tags',
    group: 'Governance',
    allowedPersonas: ['Platform Admin', 'Governance Admin']
  },
  {
    id: 'context_catalog',
    title: 'Context Catalog',
    icon: <Library className="w-5 h-5" />,
    path: '/governance/context-catalog',
    group: 'Governance',
    allowedPersonas: ['Platform Admin', 'Governance Admin']
  },

  // Admin - Restricted
  {
    id: 'admin',
    title: 'Admin',
    icon: <Settings className="w-5 h-5" />,
    path: '/admin/dashboard',
    group: 'Admin',
    allowedPersonas: ['Platform Admin']
  },

  {
    id: 'training_upload',
    title: 'Training Upload',
    icon: <Upload className="w-5 h-5" />,
    path: '/admin/training',
    group: 'Admin',
    allowedPersonas: ['Platform Admin']
  },
];

interface SidebarProps {
  collapsed: boolean;
  onToggle: () => void;
}

// Personas that show up in the dev-mode role override list. Kept in
// sync with the legacy Header component so behavior is identical.
const DEV_MODE_PERSONAS: { label: string; value: UserPersona }[] = [
  { label: 'Platform Admin', value: 'Platform Admin' },
  { label: 'Governance Admin', value: 'Governance Admin' },
  { label: 'Security Admin', value: 'Security Admin' },
  { label: 'Finance Admin', value: 'Finance Admin' },
  { label: 'User', value: 'User' },
];

export function Sidebar({ collapsed, onToggle }: SidebarProps) {
  const location = useLocation();

  // Reactive store selectors
  const currentPersona = useUserStore((state) => state.currentPersona);
  const currentUser = useUserStore((state) => state.currentUser);
  const isDevMode = useUserStore((state) => state.isDevMode);
  const activeRoleOverride = useUserStore((state) => state.activeRoleOverride);
  const toggleDevMode = useUserStore((state) => state.toggleDevMode);
  const setRoleOverride = useUserStore((state) => state.setRoleOverride);

  const pendingCount = useRequestStore((state) =>
    state.getPendingApprovalsCount()
  );

  const { uiTabs } = useBrandingStore();
  const brandName = useBrandingStore((s) => s.brandName);
  const brandLogoUrl = useBrandingStore((s) => s.brandLogoUrl);
  const databricksWorkspaceUrl = useBrandingStore((s) => s.databricksWorkspaceUrl);
  const commandCenterUrl = useBrandingStore((s) => s.commandCenterUrl);
  const features = useBrandingStore((s) => s.features);

  // User menu (account dropdown) is click-to-toggle for accessibility.
  // Close on outside click and on Escape.
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const userMenuRef = useRef<HTMLDivElement>(null);

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

  // Close the user menu when the rail collapses or expands so it doesn't
  // dangle in a stale position relative to its trigger.
  useEffect(() => {
    setUserMenuOpen(false);
  }, [collapsed]);

  const userInitials =
    currentUser?.full_name
      ?.split(' ')
      .map((part) => part[0])
      .filter(Boolean)
      .slice(0, 2)
      .join('') || 'TH';
  const userDisplayName = currentUser?.full_name || 'Taylor Hanson';
  const userEmail = currentUser?.email || 'taylor.hanson@example.com';

  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>(
    loadCollapsedGroups
  );

  const toggleGroup = useCallback((groupId: string) => {
    setCollapsedGroups((prev) => {
      const next = { ...prev, [groupId]: !prev[groupId] };
      saveCollapsedGroups(next);
      return next;
    });
  }, []);

  // External Databricks entries depend on the configured workspace URL, so
  // they're derived per-render rather than baked into the static `navItems`.
  // They drop out silently if the workspace URL is not configured.
  const externalNavItems = React.useMemo<NavItem[]>(() => {
    const items: NavItem[] = [];
    // "Analyze and Explore" / Ask Your Data is now folded into the unified
    // chat (the Request entry), so we no longer add a dedicated in-app item.
    // When the in-app feature is disabled, fall back to an external Databricks
    // Genie deep-link so legacy environments keep a data-exploration entry.
    const askYourDataEnabled =
      uiTabs?.ask_your_data !== false && features?.ask_your_data !== false;
    if (!askYourDataEnabled) {
      const genieUrl = genieHomeUrl(databricksWorkspaceUrl);
      if (genieUrl) {
        items.push({
          id: 'databricks_genie',
          title: 'Analyze and Explore',
          icon: <Sparkles className="w-5 h-5" />,
          href: genieUrl,
          group: 'Discover & Analyze',
        });
      }
    }
    if (commandCenterUrl) {
      // Internal route — Command Center is rendered inside an iframe at
      // /command-center so the icon participates in normal active-state
      // styling and back/forward history.
      items.push({
        id: 'command_center',
        title: 'Command Center',
        icon: <LayoutDashboard className="w-5 h-5" />,
        path: '/command-center',
        group: 'Build & Customize',
      });
    }
    const lakehouseUrl = workspaceHomeUrl(databricksWorkspaceUrl);
    if (lakehouseUrl) {
      items.push({
        id: 'databricks_lakehouse',
        title: 'Lakehouse',
        icon: <Database className="w-5 h-5" />,
        href: lakehouseUrl,
        group: 'Build & Customize',
      });
    }
    return items;
  }, [databricksWorkspaceUrl, commandCenterUrl, uiTabs?.ask_your_data, features?.ask_your_data]);

  const allNavItems = React.useMemo(
    () => {
      const acronym = brandAcronym(brandName);
      const agentLabel = acronym ? `${acronym} Agent` : 'Agent';
      const items = navItems.map((item) =>
        item.id === 'home' ? { ...item, title: agentLabel } : item
      );
      return [...items, ...externalNavItems];
    },
    [externalNavItems, brandName]
  );

  // Filter items based on current persona
  const filteredItems = allNavItems.filter(item => {
    // Check if explicitly disabled in config (if not present or undefined, default to true)
    if (uiTabs && uiTabs[item.id] === false) {
      return false;
    }

    if (!item.allowedPersonas) return true;
    return item.allowedPersonas.includes(currentPersona);
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
              className="p-1.5 rounded text-nav-text-muted hover:text-white hover:bg-nav-hover transition-colors"
            >
              <ChevronsRight className="w-4 h-4" />
            </button>
          </div>
        ) : (
          // Stacked layout: a wide wordmark logo (e.g. Qualcomm) plus
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
              <span className="text-sm font-semibold text-white truncate leading-tight">
                {brandName}
              </span>
            </Link>
            <button
              type="button"
              onClick={onToggle}
              aria-label="Collapse navigation"
              aria-expanded={true}
              className="p-1.5 mt-0.5 rounded text-nav-text-muted hover:text-white hover:bg-nav-hover transition-colors shrink-0"
            >
              <ChevronsLeft className="w-4 h-4" />
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
      )}>
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
                    "w-3.5 h-3.5 transition-transform duration-200 opacity-60 group-hover:opacity-100",
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
                    ? location.pathname.startsWith('/admin') && location.pathname !== '/admin/training'
                    : location.pathname === item.path;
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
                const title = collapsed
                  ? `${item.title}${isExternal ? ' (opens in new tab)' : ''}`
                  : undefined;
                const body = (
                  <>
                    {renderedIcon}
                    {!collapsed && (
                      <span className="flex-1 flex items-center gap-1.5">
                        <span>{item.title}</span>
                        {isExternal && (
                          <ExternalLink className="w-3 h-3 text-nav-text-muted shrink-0" />
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
                      title={title}
                    >
                      {body}
                    </a>
                  );
                }
                return (
                  <Link
                    key={itemKey}
                    to={item.path!}
                    className={className}
                    title={title}
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

      {/* ─── Bottom-aligned footer: notifications + user ───────── */}
      <div
        className={cn(
          "shrink-0 border-t border-nav-border",
          collapsed ? "p-2 space-y-1" : "p-3 space-y-1"
        )}
      >
        <Link
          to="/approvals"
          aria-label={
            pendingCount > 0
              ? `Notifications (${pendingCount} pending)`
              : "Notifications"
          }
          title={
            collapsed && pendingCount > 0
              ? `Notifications (${pendingCount} pending)`
              : collapsed
                ? "Notifications"
                : undefined
          }
          className={cn(
            "flex items-center text-sm transition-colors text-gray-300 hover:bg-nav-hover hover:text-white",
            collapsed
              ? "relative justify-center w-10 h-10 mx-auto rounded-lg bg-white/5"
              : "gap-3 px-3 py-2 rounded-md"
          )}
        >
          <span className="relative inline-flex">
            <Bell className={cn(collapsed ? "w-6 h-6" : "w-5 h-5")} />
            {pendingCount > 0 && (
              <span className="absolute -top-1.5 -right-1.5 min-w-[16px] h-4 px-1 bg-red-500 text-white text-[10px] font-bold rounded-full flex items-center justify-center leading-none">
                {pendingCount > 9 ? '9+' : pendingCount}
              </span>
            )}
          </span>
          {!collapsed && (
            <span className="flex-1 truncate">Notifications</span>
          )}
        </Link>

        <div ref={userMenuRef} className="relative">
          <button
            type="button"
            onClick={() => setUserMenuOpen((prev) => !prev)}
            aria-haspopup="menu"
            aria-expanded={userMenuOpen}
            aria-label={`Account menu for ${userDisplayName}`}
            title={collapsed ? userDisplayName : undefined}
            className={cn(
              "w-full flex items-center text-sm transition-colors text-gray-200 hover:bg-nav-hover hover:text-white",
              collapsed
                ? "justify-center p-1 rounded-lg"
                : "gap-3 px-2 py-2 rounded-md"
            )}
          >
            <span className="w-8 h-8 shrink-0 rounded-full bg-accent text-white flex items-center justify-center text-xs font-semibold shadow-sm">
              {userInitials}
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

              <div className="px-2 pt-2">
                <button
                  type="button"
                  className="w-full text-left px-3 py-2 text-sm text-gray-600 hover:bg-gray-50 rounded-md transition-colors font-medium"
                >
                  Settings
                </button>
                <button
                  type="button"
                  className="w-full text-left px-3 py-2 text-sm text-red-600 hover:bg-red-50 rounded-md transition-colors font-medium"
                >
                  Sign out
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
