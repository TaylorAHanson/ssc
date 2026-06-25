import { create } from 'zustand';
import { getBranding } from '../services/api';
import type { SelfServiceCenterConfig, CommunityLinksConfig } from '../services/api';
import type { UserPersona } from '../types';

/** A config-driven, iframe-embedded app surfaced in the sidebar. */
export interface EmbeddedApp {
    id: string;
    title: string;
    url: string;
    icon: string;
    group: string;
    description: string;
    allowedPersonas?: UserPersona[];
}

interface BrandingState {
    brandName: string;
    brandShortName: string;
    brandLogoUrl: string;
    brandColorPrimary: string;
    brandColorSecondary: string;
    brandColorInfo: string;
    brandColorAlert: string;
    brandColorWarning: string;
    brandColorSuccess: string;
    databricksWorkspaceUrl: string;
    embeddedApps: EmbeddedApp[];
    genieFullExperienceUrl: string;
    /** Client-side Genie poll window (seconds) before a timeout is surfaced. */
    geniePollTimeoutSeconds: number;
    features: Record<string, boolean>;
    tools: Record<string, boolean>;
    uiTabs: Record<string, boolean>;
    selfServiceCenter: SelfServiceCenterConfig;
    communityLinks: CommunityLinksConfig;
    workflowAuthoringLocked: boolean;
    isLoading: boolean;
    hasLoaded: boolean;
    error: string | null;
    fetchBranding: () => Promise<void>;
}

export const useBrandingStore = create<BrandingState>((set) => ({
    brandName: 'Self Service Hub',
    brandShortName: 'Self Service Hub',
    brandLogoUrl: '',
    brandColorPrimary: '#FF3621',
    brandColorSecondary: '#1B5162',
    brandColorInfo: '#1B5162',
    brandColorAlert: '#98102A',
    brandColorWarning: '#FFAB00',
    brandColorSuccess: '#00A972',
    databricksWorkspaceUrl: '',
    embeddedApps: [],
    genieFullExperienceUrl: '',
    geniePollTimeoutSeconds: 300,
    features: {},
    tools: {},
    uiTabs: {},
    selfServiceCenter: {},
    communityLinks: {},
    workflowAuthoringLocked: false,
    isLoading: false,
    hasLoaded: false,
    error: null,
    fetchBranding: async () => {
        set({ isLoading: true, error: null });
        try {
            const branding = await getBranding();
            set({
                brandName: branding.brand_name,
                brandShortName: branding.brand_short_name || branding.brand_name,
                brandLogoUrl: branding.brand_logo_url || '',
                brandColorPrimary: branding.brand_color_primary,
                brandColorSecondary: branding.brand_color_secondary,
                brandColorInfo: branding.brand_color_info,
                brandColorAlert: branding.brand_color_alert,
                brandColorWarning: branding.brand_color_warning,
                brandColorSuccess: branding.brand_color_success,
                databricksWorkspaceUrl: branding.databricks_workspace_url || '',
                embeddedApps: (branding.embedded_apps || [])
                    .filter((a) => a && a.id && a.url)
                    .map((a) => ({
                        id: a.id,
                        title: a.title || a.id,
                        url: a.url,
                        icon: a.icon || 'LayoutDashboard',
                        group: a.group || 'Build & Customize',
                        description: a.description || '',
                        allowedPersonas: Array.isArray(a.allowed_personas) && a.allowed_personas.length
                            ? (a.allowed_personas as UserPersona[])
                            : undefined,
                    })),
                genieFullExperienceUrl: (branding as { genie_full_experience_url?: string }).genie_full_experience_url || '',
                geniePollTimeoutSeconds: (() => {
                    const v = (branding as { genie_poll_timeout_seconds?: number }).genie_poll_timeout_seconds;
                    return typeof v === 'number' && v > 0 ? v : 300;
                })(),
                features: branding.features || {},
                tools: branding.tools || {},
                uiTabs: branding.ui?.tabs || {},
                selfServiceCenter: branding.self_service_center || {},
                communityLinks: branding.community_links || {},
                workflowAuthoringLocked: branding.workflow_authoring_locked ?? false,
                isLoading: false,
                hasLoaded: true,
            });

            // Apply colors to CSS variables for Tailwind and other usages
            document.documentElement.style.setProperty('--brand-primary', branding.brand_color_primary);
            document.documentElement.style.setProperty('--brand-secondary', branding.brand_color_secondary);
            document.documentElement.style.setProperty('--brand-info', branding.brand_color_info);
            document.documentElement.style.setProperty('--brand-alert', branding.brand_color_alert);
            document.documentElement.style.setProperty('--brand-warning', branding.brand_color_warning);
            document.documentElement.style.setProperty('--brand-success', branding.brand_color_success);

            // Update title and favicon. Only set the favicon when a brand logo
            // is actually configured — with none provided we show nothing
            // rather than falling back to a bundled default logo.
            document.title = branding.brand_name;
            const favicon = document.querySelector('link[rel="icon"]');
            if (favicon && branding.brand_logo_url) {
                favicon.setAttribute('href', branding.brand_logo_url);
            }
        } catch (error) {
            set({
                error: error instanceof Error ? error.message : 'Failed to fetch branding',
                isLoading: false,
                hasLoaded: true,
            });
        }
    },
}));
