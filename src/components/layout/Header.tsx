import { useState } from 'react';
import { Bell, X, Settings } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useRequestStore } from '../../stores/requestStore';
import { useUserStore } from '../../stores/userStore';
import { AppSwitcher } from './AppSwitcher';

export function Header() {
  const pendingCount = useRequestStore((state) => state.getPendingApprovalsCount());
  const bannerData = useRequestStore((state) => state.bannerData);

  // Use selectors for better reactivity and debugging
  const currentPersona = useUserStore((state) => state.currentPersona);
  const currentUser = useUserStore((state) => state.currentUser);
  const isDevMode = useUserStore((state) => state.isDevMode);
  const activeRoleOverride = useUserStore((state) => state.activeRoleOverride);
  const toggleDevMode = useUserStore((state) => state.toggleDevMode);
  const setRoleOverride = useUserStore((state) => state.setRoleOverride);

  const [isDismissed, setIsDismissed] = useState(false);

  // Define personas based on roles
  const personas = [
    { label: 'Platform Admin', value: 'platform_admin' },
    { label: 'Governance Admin', value: 'governance_admin' },
    { label: 'Security Admin', value: 'security_admin' },
    { label: 'Finance Admin', value: 'finance_admin' },
    { label: 'Business User', value: 'business_user' },
  ];

  const getBannerConfig = (type?: string) => {
    switch (type) {
      case 'alert':
        return { color: 'var(--brand-alert)', bg: 'color-mix(in srgb, var(--brand-alert), transparent 85%)', border: 'color-mix(in srgb, var(--brand-alert), transparent 70%)' };
      case 'warning':
        return { color: 'var(--brand-warning)', bg: 'color-mix(in srgb, var(--brand-warning), transparent 85%)', border: 'color-mix(in srgb, var(--brand-warning), transparent 70%)' };
      case 'success':
        return { color: 'var(--brand-success)', bg: 'color-mix(in srgb, var(--brand-success), transparent 85%)', border: 'color-mix(in srgb, var(--brand-success), transparent 70%)' };
      case 'info':
      default:
        return { color: 'var(--brand-info)', bg: 'color-mix(in srgb, var(--brand-info), transparent 85%)', border: 'color-mix(in srgb, var(--brand-info), transparent 70%)' };
    }
  };

  const showBanner = bannerData && bannerData.active && bannerData.message && !isDismissed;
  const bannerConfig = getBannerConfig(bannerData?.type);

  return (
    <header className="bg-white border-b border-gray-200">
      <div className="flex items-center justify-between px-6 py-4">
        {showBanner && (
          <div
            style={{
              backgroundColor: bannerConfig.bg,
              borderColor: bannerConfig.border,
              color: bannerConfig.color
            }}
            className="px-4 py-2 rounded-md border flex items-center gap-3 flex-1 max-w-2xl shadow-sm"
          >
            <p className="text-sm flex-1 font-bold">{bannerData.message}</p>
            <button
              onClick={() => setIsDismissed(true)}
              className="hover:opacity-70 transition-opacity flex-shrink-0"
              aria-label="Dismiss banner"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        )}
        {!showBanner && <div className="flex-1" />}
        <div className="flex items-center gap-4">
          <AppSwitcher />
          <Link
            to="/approvals"
            className="relative p-2 rounded-md hover:bg-gray-100 cursor-pointer transition-colors"
          >
            <Bell className="w-5 h-5 text-gray-600" />
            {pendingCount > 0 && (
              <span className="absolute -top-1 -right-1 w-5 h-5 bg-red-500 text-white text-xs font-bold rounded-full flex items-center justify-center">
                {pendingCount > 9 ? '9+' : pendingCount}
              </span>
            )}
          </Link>

          <div className="relative group">
            <div className="flex items-center gap-3 px-3 py-2 rounded-md hover:bg-gray-100 cursor-pointer transition-colors border border-transparent group-hover:border-gray-100 group-hover:bg-gray-50">
              <div className="w-8 h-8 rounded-full bg-primary text-white flex items-center justify-center text-sm font-semibold shadow-sm overflow-hidden">
                {currentUser?.full_name?.split(' ').map(n => n[0]).join('') || 'TH'}
              </div>
              <div className="flex flex-col items-start min-w-[100px]">
                <span className="text-sm font-bold text-gray-800 leading-tight truncate max-w-[120px]">
                  {currentUser?.full_name || 'Taylor Hanson'}
                </span>
                <span className="text-[10px] text-gray-500 uppercase tracking-tighter font-bold">{currentPersona}</span>
              </div>
            </div>

            {/* User Dropdown */}
            <div className="absolute right-0 top-full pt-2 w-64 hidden group-hover:block z-50 animate-in fade-in slide-in-from-top-1 duration-200">
              <div className="bg-white rounded-lg shadow-xl border border-gray-200 py-2">

                <div className="px-4 py-3 border-b border-gray-100">
                  <p className="text-xs font-semibold text-gray-400 uppercase">Account</p>
                  <p className="text-sm font-medium text-gray-900 truncate mt-1">{currentUser?.email || 'taylor.hanson@example.com'}</p>
                </div>

                {/* Dev Mode Section */}
                <div className="px-4 py-3 border-b border-gray-100 bg-gray-50/50">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Settings className="w-3.5 h-3.5 text-gray-400" />
                      <span className="text-xs font-bold text-gray-700">Dev Persona Mode</span>
                    </div>
                    <button
                      type="button"
                      className={`w-8 h-4 rounded-full p-0.5 transition-colors focus:outline-none ${isDevMode ? 'bg-blue-600' : 'bg-gray-300'}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        toggleDevMode();
                      }}
                    >
                      <div className={`w-3 h-3 bg-white rounded-full shadow-sm transition-transform duration-200 ${isDevMode ? 'translate-x-4' : 'translate-x-0'}`} />
                    </button>
                  </div>

                  {isDevMode && (
                    <div className="mt-3 space-y-2 animate-in fade-in slide-in-from-top-2">
                      <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider">Simulate Role</p>
                      <div className="grid grid-cols-1 gap-1">
                        <button
                          onClick={() => setRoleOverride(null)}
                          className={`text-left px-2 py-1.5 text-xs rounded-md transition-colors ${!activeRoleOverride ? 'bg-blue-100 text-blue-700 font-bold' : 'hover:bg-white text-gray-600 shadow-sm border border-gray-100'}`}
                        >
                          Default (My Real Roles)
                        </button>
                        {personas.map((p) => (
                          <button
                            key={p.value}
                            onClick={() => setRoleOverride(p.value)}
                            className={`text-left px-2 py-1.5 text-xs rounded-md transition-colors ${activeRoleOverride === p.value ? 'bg-blue-100 text-blue-700 font-bold' : 'hover:bg-white text-gray-600 shadow-sm border border-gray-100'}`}
                          >
                            {p.label}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>

                <div className="px-2 pt-2">
                  <button className="w-full text-left px-3 py-2 text-sm text-gray-600 hover:bg-gray-50 rounded-md transition-colors font-medium">
                    Settings
                  </button>
                  <button className="w-full text-left px-3 py-2 text-sm text-red-600 hover:bg-red-50 rounded-md transition-colors font-medium">
                    Sign out
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </header>
  );
}
