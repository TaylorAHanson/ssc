import { useEffect } from 'react';
import { Navigate, useParams, useNavigate } from 'react-router-dom';
import { useRequestStore } from '../stores/requestStore';
import { MessageSquarePlus, SlidersHorizontal } from 'lucide-react';
import { AdminDashboard } from './admin/AdminDashboard';
import { FeedbackAdmin } from './admin/FeedbackAdmin';
import { Settings } from './admin/Settings';
import { LEGACY_DASHBOARDS_GROUP, TRAINING_GROUP, settingsGroupPath } from './admin/settingsGroups';
import { useBrandingStore } from '../stores/brandingStore';

// Tabs that moved, so old links and bookmarks still land somewhere useful.
const MOVED_TABS: Record<string, string> = {
  'test-runner': '/admin/dashboard',
  'legacy-dashboards': settingsGroupPath(LEGACY_DASHBOARDS_GROUP),
  training: settingsGroupPath(TRAINING_GROUP),
};

export function Admin() {
  const fetchRequests = useRequestStore((state) => state.fetchRequests);
  const fetchApprovals = useRequestStore((state) => state.fetchApprovals);

  useEffect(() => {
    fetchRequests();
    fetchApprovals();
  }, [fetchRequests, fetchApprovals]);

  const { tab } = useParams<{ tab: string }>();
  const navigate = useNavigate();
  const uiTabs = useBrandingStore((s) => s.uiTabs);
  const feedbackEnabled = uiTabs?.feedback !== false;

  const validTabs = ['dashboard', 'settings', ...(feedbackEnabled ? ['feedback'] : [])];
  const activeTab = tab && validTabs.includes(tab) ? tab as 'dashboard' | 'settings' | 'feedback' : 'dashboard';

  const handleTabChange = (newTab: string) => {
    navigate(`/admin/${newTab}`);
  };

  if (tab && MOVED_TABS[tab]) return <Navigate to={MOVED_TABS[tab]} replace />;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-900 mb-2">Admin Dashboard</h1>
        <p className="text-gray-600">Manage requests, system messages, and features</p>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 border-b border-gray-200">
        <button
          onClick={() => handleTabChange('dashboard')}
          className={`px-4 py-2 font-medium text-sm transition-colors ${activeTab === 'dashboard'
            ? 'border-b-2 border-primary text-primary'
            : 'text-gray-600 hover:text-gray-900'
            }`}
        >
          Dashboard
        </button>
        <button
          onClick={() => handleTabChange('settings')}
          className={`px-4 py-2 font-medium text-sm transition-colors ${activeTab === 'settings'
            ? 'border-b-2 border-primary text-primary'
            : 'text-gray-600 hover:text-gray-900'
            }`}
        >
          <SlidersHorizontal className="w-4 h-4 inline mr-2" />
          Settings
        </button>
        {feedbackEnabled && (
          <button
            onClick={() => handleTabChange('feedback')}
            className={`px-4 py-2 font-medium text-sm transition-colors ${activeTab === 'feedback'
              ? 'border-b-2 border-primary text-primary'
              : 'text-gray-600 hover:text-gray-900'
              }`}
          >
            <MessageSquarePlus className="w-4 h-4 inline mr-2" />
            Feedback
          </button>
        )}
      </div>

      {activeTab === 'dashboard' && <AdminDashboard />}
      {activeTab === 'settings' && <Settings />}
      {activeTab === 'feedback' && <FeedbackAdmin />}
    </div>
  );
}
