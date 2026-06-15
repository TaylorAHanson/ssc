import React from 'react';
import {
  LayoutDashboard,
  BarChart,
  Database,
  Sparkles,
  Wrench,
  ShieldCheck,
  ShieldAlert,
  FileText,
  Calendar,
  MessageSquare,
  Library,
  Tags,
  Search,
  Home,
  List,
  CheckCircle2,
  GraduationCap,
  Settings,
  Bell,
  Globe,
  Activity,
  Gauge,
  LineChart,
  PieChart,
  Table,
  Boxes,
  Workflow,
  Cloud,
  Link as LinkIcon,
  AppWindow,
  type LucideIcon,
} from 'lucide-react';

// Curated allowlist of lucide icons that config-driven nav entries (e.g.
// `embedded_apps[].icon`) may reference by name. Keeping this an explicit map
// — rather than importing all of lucide — keeps the bundle small and means an
// unknown/typo'd name degrades gracefully to a sensible default instead of
// crashing the sidebar. Names are matched case-insensitively.
const ICON_MAP: Record<string, LucideIcon> = {
  layoutdashboard: LayoutDashboard,
  barchart: BarChart,
  database: Database,
  sparkles: Sparkles,
  wrench: Wrench,
  shieldcheck: ShieldCheck,
  shieldalert: ShieldAlert,
  filetext: FileText,
  calendar: Calendar,
  messagesquare: MessageSquare,
  library: Library,
  tags: Tags,
  search: Search,
  home: Home,
  list: List,
  checkcircle2: CheckCircle2,
  graduationcap: GraduationCap,
  settings: Settings,
  bell: Bell,
  globe: Globe,
  activity: Activity,
  gauge: Gauge,
  linechart: LineChart,
  piechart: PieChart,
  table: Table,
  boxes: Boxes,
  workflow: Workflow,
  cloud: Cloud,
  link: LinkIcon,
  appwindow: AppWindow,
};

const DEFAULT_ICON = AppWindow;

/** Resolve a lucide icon name (from config) to its component, with fallback. */
export function resolveNavIcon(name?: string): LucideIcon {
  if (!name) return DEFAULT_ICON;
  return ICON_MAP[name.replace(/[\s_-]+/g, '').toLowerCase()] ?? DEFAULT_ICON;
}

/** Render a config-driven nav icon as an element at the given size class. */
export function renderNavIcon(
  name: string | undefined,
  className = 'w-5 h-5'
): React.ReactNode {
  const Icon = resolveNavIcon(name);
  return <Icon className={className} />;
}
