import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import type { User, RoleMapping, UserPersona } from '../types';
import { userService } from '../services/userService';

interface UserState {
    currentUser: User | null;
    currentPersona: UserPersona;
    roleMappings: RoleMapping[];
    isLoading: boolean;
    isInitialized: boolean;
    error: string | null;

    fetchCurrentUser: () => Promise<void>;
    fetchRoleMappings: () => Promise<void>;
    createRoleMapping: (externalRole: string, internalRole: string) => Promise<void>;
    updateRoleMapping: (id: number, externalRole: string, internalRole: string) => Promise<void>;
    deleteRoleMapping: (id: number) => Promise<void>;

    // Dev Mode
    isDevMode: boolean;
    activeRoleOverride: string | null;
    toggleDevMode: () => void;
    setRoleOverride: (role: string | null) => Promise<void>;
    hydrated: boolean;
    setHydrated: (val: boolean) => void;

    // Landing page preference (persisted). Empty string means "no preference".
    defaultHomePage: string;
    setDefaultHomePage: (val: string) => void;
}

const derivePersona = (user: User | null): UserPersona => {
    if (!user) return 'User';
    if (user.roles.includes('Platform Admin')) return 'Platform Admin';
    if (user.roles.includes('Governance Admin')) return 'Governance Admin';
    if (user.roles.includes('Security Admin')) return 'Security Admin';
    if (user.roles.includes('Finance Admin')) return 'Finance Admin';
    return 'User';
};

export const useUserStore = create<UserState>()(
    persist(
        (set, get) => ({
            currentUser: null,
            currentPersona: 'User',
            roleMappings: [],
            isLoading: false,
            error: null,
            hydrated: false,
            setHydrated: (val: boolean) => set({ hydrated: val }),

            // Dev Mode
            isDevMode: false,
            activeRoleOverride: null,

            // Landing page preference
            defaultHomePage: '',
            setDefaultHomePage: (val: string) => set({ defaultHomePage: val }),

            toggleDevMode: async () => {
                const nextIsDevMode = !get().isDevMode;
                let nextRoleOverride = get().activeRoleOverride;

                // Auto-select Platform Admin if enabling dev mode and no override selected
                if (nextIsDevMode && !nextRoleOverride) {
                    nextRoleOverride = 'Platform Admin';
                }

                set({ isDevMode: nextIsDevMode, activeRoleOverride: nextRoleOverride });
                await get().fetchCurrentUser();
            },

            isInitialized: false,

            setRoleOverride: async (role: string | null) => {
                set({ activeRoleOverride: role });
                await get().fetchCurrentUser();
            },

            fetchCurrentUser: async () => {
                try {
                    // Don't fetch until store is hydrated from localStorage
                    if (!get().hydrated) {
                        console.log('[userStore] Skipping fetch: not yet hydrated');
                        return;
                    }

                    set({ isLoading: true, error: null });
                    const { activeRoleOverride, isDevMode } = get();

                    const overrideRole = isDevMode ? activeRoleOverride : null;
                    const user = await userService.getMe(overrideRole);

                    set({
                        currentUser: user,
                        currentPersona: derivePersona(user),
                        isLoading: false,
                        isInitialized: true,
                        error: null
                    });
                } catch (error: any) {
                    const isDefinitiveError =
                        error.message.includes('401') ||
                        error.message.includes('404') ||
                        error.message.includes('Unauthorized');

                    set({
                        error: error.message,
                        isLoading: false,
                        isInitialized: isDefinitiveError
                    });

                    // Log the error for debugging reset issues
                    console.error('[userStore] Fetch current user failed:', error.message);
                }
            },

            fetchRoleMappings: async () => {
                try {
                    set({ isLoading: true, error: null });
                    const roleMappings = await userService.getRoleMappings();
                    set({ roleMappings, isLoading: false });
                } catch (error: any) {
                    set({ error: error.message, isLoading: false });
                }
            },

            createRoleMapping: async (externalRole: string, internalRole: string) => {
                try {
                    set({ isLoading: true, error: null });
                    await userService.createRoleMapping(externalRole, internalRole);
                    await get().fetchRoleMappings();
                } catch (error: any) {
                    set({ error: error.message, isLoading: false });
                    throw error;
                }
            },

            updateRoleMapping: async (id: number, externalRole: string, internalRole: string) => {
                try {
                    set({ isLoading: true, error: null });
                    await userService.updateRoleMapping(id, externalRole, internalRole);
                    await get().fetchRoleMappings();
                } catch (error: any) {
                    set({ error: error.message, isLoading: false });
                    throw error;
                }
            },

            deleteRoleMapping: async (id: number) => {
                try {
                    set({ isLoading: true, error: null });
                    await userService.deleteRoleMapping(id);
                    await get().fetchRoleMappings();
                } catch (error: any) {
                    set({ error: error.message, isLoading: false });
                    throw error;
                }
            }
        }),
        {
            name: 'user-storage',
            storage: createJSONStorage(() => localStorage),
            partialize: (state) => ({
                isDevMode: state.isDevMode,
                activeRoleOverride: state.activeRoleOverride,
                defaultHomePage: state.defaultHomePage,
            }),
            onRehydrateStorage: () => (state) => {
                // Migrate legacy `/home` preference to the new `/request` path.
                if (state?.defaultHomePage === '/home') {
                    state.setDefaultHomePage('/request');
                }
                state?.setHydrated(true);
            },
        }
    )
);
