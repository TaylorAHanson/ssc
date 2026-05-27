import { useLocation } from 'react-router-dom';
import { useBrandingStore } from '../../stores/brandingStore';

export interface CurrentApp {
    id: string;
    name: string;
    sub_label?: string;
    icon: string;
    path: string;
    accent_color: string;
    accent_bg: string;
}

const DEFAULT_CURRENT_APP: CurrentApp = {
    id: 'self-service',
    name: 'Discover & Request',
    sub_label: 'Self service',
    icon: 'Layout',
    path: '/',
    accent_color: '#2563eb',
    accent_bg: '#eff6ff',
};

// Paths owned by the "self-service" (root, `/`) app. Listed explicitly so the
// path matcher can distinguish e.g. `/admin/...` (Command Center) from the
// rest of the self-service experience.
const SELF_SERVICE_PATHS = [
    '/',
    '/request',
    '/discovery',
    '/requests',
    '/approvals',
    '/reports',
    '/apps',
];

/**
 * Resolve the active entry from `ui.app_switcher` based on the current route.
 *
 * Internal apps (those with a `path`) are matched by longest-prefix; the root
 * `/` app additionally claims the self-service routes listed above. External
 * apps (links) are never returned. Falls back to a built-in default so the
 * caller always gets something to render even before branding loads.
 */
export function useCurrentApp(): CurrentApp {
    const location = useLocation();
    const uiAppSwitcher = useBrandingStore((state) => state.uiAppSwitcher);

    const internalApps = (uiAppSwitcher || []).filter((a: any) => !!a.path);
    if (internalApps.length === 0) return DEFAULT_CURRENT_APP;

    const sorted = [...internalApps].sort(
        (a: any, b: any) => (b.path?.length || 0) - (a.path?.length || 0)
    );
    const match = sorted.find((a: any) => {
        if (a.path === '/') {
            return (
                SELF_SERVICE_PATHS.includes(location.pathname) ||
                location.pathname.startsWith('/community/')
            );
        }
        return location.pathname.startsWith(a.path);
    });

    return (match as CurrentApp) || (internalApps[0] as CurrentApp);
}
