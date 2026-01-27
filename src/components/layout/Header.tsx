import { useState } from 'react';
import { Bell, X } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useRequestStore } from '../../stores/requestStore';
import { useUserStore } from '../../stores/userStore';

export function Header() {
  const pendingCount = useRequestStore((state) => state.getPendingApprovalsCount());
  const bannerData = useRequestStore((state) => state.bannerData);
  const { currentPersona, setPersona } = useUserStore();
  const [isDismissed, setIsDismissed] = useState(false);

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
            <div className="flex items-center gap-3 px-3 py-2 rounded-md hover:bg-gray-100 cursor-pointer transition-colors">
              <div className="w-8 h-8 rounded-full bg-primary text-white flex items-center justify-center text-sm font-semibold">
                TH
              </div>
              <div className="flex flex-col items-start">
                <span className="text-sm font-medium text-gray-700">User Profile</span>
                <span className="text-xs text-gray-500">{currentPersona}</span>
              </div>
            </div>

            {/* Persona Switcher Dropdown */}
            <div className="absolute right-0 top-full pt-2 w-48 hidden group-hover:block z-50">
              <div className="bg-white rounded-md shadow-lg border border-gray-200 py-1">
                <div className="px-4 py-2 text-xs font-semibold text-gray-500 uppercase tracking-wider border-b border-gray-100">
                  Switch Persona
                </div>
                {['Business User', 'Power User', 'Platform Admin'].map((persona) => (
                  <button
                    key={persona}
                    onClick={() => setPersona(persona as any)}
                    className={`w-full text-left px-4 py-2 text-sm hover:bg-gray-50 flex items-center justify-between ${currentPersona === persona ? 'text-primary font-medium' : 'text-gray-700'
                      }`}
                  >
                    {persona}
                    {currentPersona === persona && <div className="w-2 h-2 rounded-full bg-primary" />}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </header >
  );
}

