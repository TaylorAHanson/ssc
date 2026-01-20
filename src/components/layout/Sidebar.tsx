import { useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { 
  ChevronLeft, 
  ChevronRight, 
  Database, 
  Cloud, 
  Key,
  ShoppingCart,
  Globe,
  Download,
  FileText,
  MessageSquare,
  Settings,
  Home,
  List,
  CheckCircle2,
  GraduationCap,
  Calendar,
  Code
} from 'lucide-react';
import { cn } from '../../lib/utils';

interface NavItem {
  title: string;
  icon: React.ReactNode;
  path: string;
  group: string;
}

const navItems: NavItem[] = [
  // Main
  { title: 'Home', icon: <Home className="w-5 h-5" />, path: '/', group: 'Main' },
  { title: 'My Requests', icon: <List className="w-5 h-5" />, path: '/requests', group: 'Main' },
  { title: 'Pending Approvals', icon: <CheckCircle2 className="w-5 h-5" />, path: '/approvals', group: 'Main' },
  
  // Enterprise Data
  { title: 'Request Data Access', icon: <Key className="w-5 h-5" />, path: '/paas/request-access', group: 'Enterprise Data' },
  { title: 'Marketplace Certification', icon: <ShoppingCart className="w-5 h-5" />, path: '/paas/marketplace', group: 'Enterprise Data' },

  // PaaS
  { title: 'Get Workspace Access', icon: <Database className="w-5 h-5" />, path: '/paas/workspace-access', group: 'PaaS' },
  { title: 'Create Catalog/Schema/Table', icon: <Database className="w-5 h-5" />, path: '/paas/request-catalog', group: 'PaaS' },
  { title: 'Provision New Workspace', icon: <Cloud className="w-5 h-5" />, path: '/paas/provision-workspace', group: 'PaaS' },
  { title: 'Provision Service Principal', icon: <Key className="w-5 h-5" />, path: '/paas/service-principal', group: 'PaaS' },
  { title: 'GitHub Repository Creation', icon: <Code className="w-5 h-5" />, path: '/paas/github-repo-creation', group: 'PaaS' },
  { title: 'Request REST API Access', icon: <Globe className="w-5 h-5" />, path: '/daas/rest-api', group: 'PaaS' },
  { title: 'Request Batch Data Access', icon: <Download className="w-5 h-5" />, path: '/daas/batch-data', group: 'PaaS' },
  
  // Community
  { title: 'Training', icon: <GraduationCap className="w-5 h-5" />, path: '/community/training', group: 'Community' },
  { title: 'Event Calendar', icon: <Calendar className="w-5 h-5" />, path: '/community/events', group: 'Community' },
  { title: 'Templates & Assets', icon: <FileText className="w-5 h-5" />, path: '/community/assets', group: 'Community' },
  { title: 'Community Links', icon: <MessageSquare className="w-5 h-5" />, path: '/community/links', group: 'Community' },
  
  // Admin
  { title: 'Admin', icon: <Settings className="w-5 h-5" />, path: '/admin', group: 'Admin' },
];

export function Sidebar() {
  const [collapsed, setCollapsed] = useState(false);
  const location = useLocation();

  const groupedItems = navItems.reduce((acc, item) => {
    if (!acc[item.group]) {
      acc[item.group] = [];
    }
    acc[item.group].push(item);
    return acc;
  }, {} as Record<string, NavItem[]>);

  return (
    <div
      className={cn(
        "bg-white border-r border-gray-200 transition-all duration-300 flex flex-col",
        collapsed ? "w-16" : "w-72"
      )}
    >
      <div className="flex items-center justify-between p-4 border-b border-gray-200">
        {!collapsed && (
          <h2 className="text-lg font-semibold text-primary">EDAS Self Service Hub</h2>
        )}
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="p-1 rounded-md hover:bg-gray-100"
        >
          {collapsed ? (
            <ChevronRight className="w-5 h-5" />
          ) : (
            <ChevronLeft className="w-5 h-5" />
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
    </div>
  );
}

