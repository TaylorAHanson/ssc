import { create } from 'zustand';
import { getBranding } from '../services/api';
import defaultLogo from '../assets/icon.svg';

interface BrandingState {
    brandName: string;
    brandLogoUrl: string;
    brandColorPrimary: string;
    brandColorSecondary: string;
    brandColorInfo: string;
    brandColorAlert: string;
    brandColorWarning: string;
    brandColorSuccess: string;
    isLoading: boolean;
    hasLoaded: boolean;
    error: string | null;
    fetchBranding: () => Promise<void>;
}

export const useBrandingStore = create<BrandingState>((set) => ({
    brandName: 'ATLAS',
    brandLogoUrl: defaultLogo,
    brandColorPrimary: '#FF3621',
    brandColorSecondary: '#1B5162',
    brandColorInfo: '#1B5162',
    brandColorAlert: '#98102A',
    brandColorWarning: '#FFAB00',
    brandColorSuccess: '#00A972',
    isLoading: false,
    hasLoaded: false,
    error: null,
    fetchBranding: async () => {
        set({ isLoading: true, error: null });
        try {
            const branding = await getBranding();
            set({
                brandName: branding.brand_name,
                brandLogoUrl: branding.brand_logo_url || defaultLogo,
                brandColorPrimary: branding.brand_color_primary,
                brandColorSecondary: branding.brand_color_secondary,
                brandColorInfo: branding.brand_color_info,
                brandColorAlert: branding.brand_color_alert,
                brandColorWarning: branding.brand_color_warning,
                brandColorSuccess: branding.brand_color_success,
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

            // Update title and favicon
            document.title = branding.brand_name;
            const logoUrl = branding.brand_logo_url || defaultLogo;
            const favicon = document.querySelector('link[rel="icon"]');
            if (favicon) {
                favicon.setAttribute('href', logoUrl);
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
