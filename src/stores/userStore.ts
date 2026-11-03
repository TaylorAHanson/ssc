import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import { ROLE_PRIORITY } from '../types';
import type { User, RoleMapping, UserPersona } from '../types';
import { userService } from '../services/userService';
import { errorMessage } from '../lib/errors';

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
    /** Force dev mode off (e.g. when the backend reports a non-dev environment). */
    disableDevMode: () => Promise<void>;
    setRoleOverride: (role: string | null) => Promise<void>;
    hydrated: boolean;
    setHydrated: (val: boolean) => void;

    // Landing page preference (persisted). Empty string means "no preference".
    defaultHomePage: string;
    setDefaultHomePage: (val: string) => void;
}

/** The old name of Application Admin, still accepted from saved dev-mode picks. */
const LEGACY_PLATFORM_ADMIN = 'Platform Admin';

/** For display only (highest role); gate features with `can` / `hasCapability`. */
const derivePersona = (user: User | null): UserPersona => {
    if (!user) return 'User';
    const roles = user.roles.map((r) => (r === LEGACY_PLATFORM_ADMIN ? 'Application Admin' : r));
    return ROLE_PRIORITY.find((role) => role !== 'User' && roles.includes(role)) ?? 'User';
};

/** Whether the user holds any of the given capability ids (from GET /roles/me). */
export const hasCapability = (user: User | null, ...capabilityIds: string[]): boolean =>
    !!user?.capabilities && capabilityIds.some((id) => user.capabilities!.includes(id));

/** Folds a role name for comparison; the old name Platform Admin reads as Application Admin. */
const foldRole = (role: string): string => {
    const folded = role.replace(/_/g, ' ').trim().replace(/\s+/g, ' ').toLowerCase();
    return folded === 'platform admin' ? 'application admin' : folded;
};

/**
 * Whether the user holds one of `allowed` (role names from settings, e.g. an embedded
 * app's allowed personas). Super Admin passes any role check.
 */
export const matchesRoles = (user: User | null, allowed: string[]): boolean => {
    if (!user) return false;
    const mine = new Set(user.roles.map(foldRole));
    if (mine.has('super admin')) return true;
    return allowed.some((role) => mine.has(foldRole(role)));
};

/** Hook: whether the current user holds any of the given capabilities. */
export const useCan = (...capabilityIds: string[]): boolean =>
    useUserStore((s) => hasCapability(s.currentUser, ...capabilityIds));

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

                // Auto-select Super Admin if enabling dev mode and no override selected
                if (nextIsDevMode && !nextRoleOverride) {
                    nextRoleOverride = 'Super Admin';
                }

                set({ isDevMode: nextIsDevMode, activeRoleOverride: nextRoleOverride });
                await get().fetchCurrentUser();
            },

            disableDevMode: async () => {
                // Clear any stale persisted override so the client stops sending
                // X-Dev-Role-Override (the backend already ignores it outside dev
                // envs) and the displayed persona reverts to the user's real roles.
                if (!get().isDevMode && !get().activeRoleOverride) return;
                set({ isDevMode: false, activeRoleOverride: null });
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
                    if (!get().hydrated) return;

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
                } catch (error) {
                    const message = errorMessage(error, 'Failed to load the current user');
                    const isDefinitiveError =
                        message.includes('401') ||
                        message.includes('404') ||
                        message.includes('Unauthorized');

                    set({
                        error: message,
                        isLoading: false,
                        isInitialized: isDefinitiveError
                    });

                    // Log the error for debugging reset issues
                    console.error('[userStore] Fetch current user failed:', message);
                }
            },

            fetchRoleMappings: async () => {
                try {
                    set({ isLoading: true, error: null });
                    const roleMappings = await userService.getRoleMappings();
                    set({ roleMappings, isLoading: false });
                } catch (error) {
                    set({ error: errorMessage(error, 'Failed to load role mappings'), isLoading: false });
                }
            },

            createRoleMapping: async (externalRole: string, internalRole: string) => {
                try {
                    set({ isLoading: true, error: null });
                    await userService.createRoleMapping(externalRole, internalRole);
                    await get().fetchRoleMappings();
                } catch (error) {
                    set({ error: errorMessage(error, 'Failed to save the role mapping'), isLoading: false });
                    throw error;
                }
            },

            updateRoleMapping: async (id: number, externalRole: string, internalRole: string) => {
                try {
                    set({ isLoading: true, error: null });
                    await userService.updateRoleMapping(id, externalRole, internalRole);
                    await get().fetchRoleMappings();
                } catch (error) {
                    set({ error: errorMessage(error, 'Failed to save the role mapping'), isLoading: false });
                    throw error;
                }
            },

            deleteRoleMapping: async (id: number) => {
                try {
                    set({ isLoading: true, error: null });
                    await userService.deleteRoleMapping(id);
                    await get().fetchRoleMappings();
                } catch (error) {
                    set({ error: errorMessage(error, 'Failed to save the role mapping'), isLoading: false });
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
                // The old role name: it passed every check, as Super Admin does now.
                if (state?.activeRoleOverride === LEGACY_PLATFORM_ADMIN) {
                    // Not hydrated yet, so this only stores the pick (no fetch).
                    void state.setRoleOverride('Super Admin');
                }
                state?.setHydrated(true);
            },
        }
    )
);
