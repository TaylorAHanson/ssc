import { create } from 'zustand';
import { getBranding } from '../services/api';

interface BrandingState {
    brandName: string;
    brandLogoUrl: string;
    brandColorPrimary: string;
    brandColorSecondary: string;
    isLoading: boolean;
    error: string | null;
    fetchBranding: () => Promise<void>;
}

export const useBrandingStore = create<BrandingState>((set) => ({
    brandName: 'EDAS Hub',
    brandLogoUrl: '',
    brandColorPrimary: '#3253DC',
    brandColorSecondary: '#0ea5e9',
    isLoading: false,
    error: null,
    fetchBranding: async () => {
        set({ isLoading: true, error: null });
        try {
            const branding = await getBranding();
            set({
                brandName: branding.brand_name,
                brandLogoUrl: branding.brand_logo_url,
                brandColorPrimary: branding.brand_color_primary,
                brandColorSecondary: branding.brand_color_secondary,
                isLoading: false,
            });

            // Apply primary color to CSS variable for Tailwind and other usages
            document.documentElement.style.setProperty('--brand-primary', branding.brand_color_primary);
            document.documentElement.style.setProperty('--brand-secondary', branding.brand_color_secondary);
        } catch (error) {
            set({
                error: error instanceof Error ? error.message : 'Failed to fetch branding',
                isLoading: false
            });
        }
    },
}));
