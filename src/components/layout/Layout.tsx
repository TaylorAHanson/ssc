import { useEffect, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { X } from 'lucide-react';
import { Sidebar } from './Sidebar';
import { useRequestStore } from '../../stores/requestStore';
import { cn } from '../../lib/utils';

interface LayoutProps {
  children: React.ReactNode;
}

// Routes that render their own full-bleed content (e.g. iframed apps). On
// these routes we strip the default `main` padding and disable scrolling
// so the child can occupy the entire pane edge-to-edge. We also auto-
// collapse the sidebar on entry to give the embedded view as much room
// as possible — the user can still manually re-expand it.
//
// `/embedded/*` covers all config-driven embedded apps.
function isFullBleedRoute(pathname: string): boolean {
  return pathname.startsWith('/embedded/');
}

type BannerType = 'alert' | 'warning' | 'success' | 'info';

function getBannerColors(type?: string) {
  const t: BannerType = (type as BannerType) || 'info';
  const cssVar = {
    alert: '--brand-alert',
    warning: '--brand-warning',
    success: '--brand-success',
    info: '--brand-info',
  }[t];
  return {
    color: `var(${cssVar})`,
    backgroundColor: `color-mix(in srgb, var(${cssVar}), transparent 85%)`,
    borderColor: `color-mix(in srgb, var(${cssVar}), transparent 70%)`,
  };
}

export function Layout({ children }: LayoutProps) {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const location = useLocation();
  const isFullBleed = isFullBleedRoute(location.pathname);

  const bannerData = useRequestStore((state) => state.bannerData);
  const [bannerDismissed, setBannerDismissed] = useState(false);
  const showBanner = !!(
    bannerData &&
    bannerData.active &&
    bannerData.message &&
    !bannerDismissed
  );
  const bannerColors = getBannerColors(bannerData?.type);

  useEffect(() => {
    if (isFullBleedRoute(location.pathname)) {
      setSidebarCollapsed(true);
    }
    // Intentionally only depends on pathname: we collapse on *navigation into*
    // a full-bleed route, but never fight the user if they manually re-expand
    // the rail while still on that route.
  }, [location.pathname]);

  return (
    // Side-by-side flex: sidebar owns its full vertical extent (with its
    // own brand header + footer), and the main column stacks an optional
    // banner above the page content. `min-h-0` on the row + main lets the
    // inner scrollers behave correctly without needing `overflow-hidden`
    // (which would otherwise clip the sidebar's account-menu popout).
    <div className="flex h-screen bg-surface">
      <Sidebar
        collapsed={sidebarCollapsed}
        onToggle={() => setSidebarCollapsed((prev) => !prev)}
      />
      <div className="flex-1 flex flex-col min-w-0 min-h-0">
        {showBanner && bannerData && (
          <div
            role="status"
            style={bannerColors}
            className="flex items-center gap-3 px-4 py-2 border-b shadow-sm shrink-0"
          >
            <p className="text-sm flex-1 font-bold truncate">
              {bannerData.message}
            </p>
            <button
              type="button"
              onClick={() => setBannerDismissed(true)}
              className="hover:opacity-70 transition-opacity shrink-0"
              aria-label="Dismiss banner"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        )}
        <main
          className={cn(
            'flex-1 min-h-0 bg-surface',
            isFullBleed ? 'overflow-hidden' : 'overflow-y-auto p-6'
          )}
        >
          {children}
        </main>
      </div>
    </div>
  );
}
