import { useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import {
  ChevronLeft,
  ChevronRight,
  Settings,
  Home,
  List,
  CheckCircle2,
  GraduationCap,
  Calendar,
  FileText,
  MessageSquare,
  BarChart,
  Upload,
  Search,
  ShieldCheck,
  ShieldAlert
} from 'lucide-react';
import { cn } from '../../lib/utils';

import { useBrandingStore } from '../../stores/brandingStore';
import { useUserStore } from '../../stores/userStore';
import type { UserPersona } from '../../types';

interface NavItem {
  id: string;
  title: string;
  icon: React.ReactNode;
  path: string;
  group: string;
  allowedPersonas?: UserPersona[]; // If undefined, allowed for all
}

const navItems: NavItem[] = [
  // Main
  { id: 'home', title: 'Home', icon: <Home className="w-5 h-5" />, path: '/', group: 'Main' },
  { id: 'data_discovery', title: 'Data Discovery', icon: <Search className="w-5 h-5" />, path: '/discovery', group: 'Main' },
  { id: 'my_requests', title: 'My Requests', icon: <List className="w-5 h-5" />, path: '/requests', group: 'Main' },
  { id: 'pending_approvals', title: 'Pending Approvals', icon: <CheckCircle2 className="w-5 h-5" />, path: '/approvals', group: 'Main' },
  { id: 'reports', title: 'Reports', icon: <BarChart className="w-5 h-5" />, path: '/reports', group: 'Main' },

  // Community - Available to everyone
  { id: 'training', title: 'Training', icon: <GraduationCap className="w-5 h-5" />, path: '/community/training', group: 'Community' },
  { id: 'event_calendar', title: 'Event Calendar', icon: <Calendar className="w-5 h-5" />, path: '/community/events', group: 'Community' },
  { id: 'templates_assets', title: 'Templates & Assets', icon: <FileText className="w-5 h-5" />, path: '/community/assets', group: 'Community' },
  { id: 'community_links', title: 'Community Links', icon: <MessageSquare className="w-5 h-5" />, path: '/community/links', group: 'Community' },

  // Governance - Restricted
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

  // Admin - Restricted
  {
    id: 'admin',
    title: 'Admin',
    icon: <Settings className="w-5 h-5" />,
    path: '/admin',
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

export function Sidebar() {
  const [collapsed, setCollapsed] = useState(false);
  const location = useLocation();

  // Reactive store selectors
  const currentPersona = useUserStore((state) => state.currentPersona);
  const { brandName, brandLogoUrl, uiTabs } = useBrandingStore();

  // Filter items based on current persona
  const filteredItems = navItems.filter(item => {
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

  return (
    <div
      className={cn(
        "bg-white border-r border-gray-200 transition-all duration-300 flex flex-col relative",
        collapsed ? "w-16" : "w-72"
      )}
    >
      <div className={cn("p-4 border-b border-gray-200 min-h-[65px] flex items-center justify-between", !collapsed && "items-start")}>
        <div className={cn("flex flex-1 overflow-hidden", collapsed ? "flex-col items-center" : "flex-col gap-2")}>
          {brandLogoUrl && !collapsed && (
            <img src={brandLogoUrl} alt="Logo" className="h-8 w-auto object-contain self-start" />
          )}
          {!collapsed && (
            <h2 className="text-sm font-bold text-primary leading-tight">{brandName}</h2>
          )}
        </div>
        <button
          onClick={() => setCollapsed(!collapsed)}
          className={cn("p-1 rounded-md hover:bg-gray-100", collapsed && "mx-auto mt-2")}
        >
          {collapsed ? (
            <ChevronRight className="w-5 h-5" />
          ) : (
            <ChevronLeft className="w-5 h-5 shrink-0" />
          )}
        </button>
      </div>

      <nav className="flex-1 overflow-y-auto p-4 space-y-6">
        {Object.entries(groupedItems).map(([group, items]) => (
          <div key={group}>
            {!collapsed && (
              <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">
                {group}
              </h3>
            )}
            <div className="space-y-1">
              {items.map((item) => {
                const isActive = location.pathname === item.path;
                return (
                  <Link
                    key={item.path}
                    to={item.path}
                    className={cn(
                      "flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors",
                      isActive
                        ? "bg-primary text-white"
                        : "text-gray-700 hover:bg-gray-100",
                      collapsed && "justify-center"
                    )}
                    title={collapsed ? item.title : undefined}
                  >
                    {item.icon}
                    {!collapsed && <span>{item.title}</span>}
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>
      {/* Redundant Profile Section Removed */}
    </div>
  );
}
