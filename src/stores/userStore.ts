import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import type { User, Role, UserPersona } from '../types';
import { userService } from '../services/userService';

interface UserState {
    currentUser: User | null;
    currentPersona: UserPersona;
    users: User[];
    roles: Role[];
    isLoading: boolean;
    isInitialized: boolean;
    error: string | null;

    fetchCurrentUser: () => Promise<void>;
    fetchUsers: () => Promise<void>;
    fetchRoles: () => Promise<void>;
    // Dev Mode
    isDevMode: boolean;
    activeRoleOverride: string | null;
    toggleDevMode: () => void;
    setRoleOverride: (role: string | null) => Promise<void>;
    hydrated: boolean;
    setHydrated: (val: boolean) => void;
    updateUserRoles: (userId: string, roleIds: string[]) => Promise<void>;
    createUser: (email: string, fullName: string, roleIds?: string[]) => Promise<User>;
}

const derivePersona = (user: User | null): UserPersona => {
    if (!user) return 'Business User';
    if (user.roles.some(r => r.name === 'platform_admin')) return 'Platform Admin';
    if (user.roles.some(r => r.name === 'governance_admin')) return 'Governance Admin';
    if (user.roles.some(r => r.name === 'security_admin')) return 'Security Admin';
    if (user.roles.some(r => r.name === 'finance_admin')) return 'Finance Admin';
    return 'Business User';
};

export const useUserStore = create<UserState>()(
    persist(
        (set, get) => ({
            currentUser: null,
            currentPersona: 'Business User',
            users: [],
            roles: [],
            isLoading: false,
            error: null,
            hydrated: false,
            setHydrated: (val: boolean) => set({ hydrated: val }),

            // Dev Mode
            isDevMode: false,
            activeRoleOverride: null,

            toggleDevMode: async () => {
                const nextIsDevMode = !get().isDevMode;
                let nextRoleOverride = get().activeRoleOverride;

                // Auto-select Platform Admin if enabling dev mode and no override selected
                if (nextIsDevMode && !nextRoleOverride) {
                    nextRoleOverride = 'platform_admin';
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

            fetchUsers: async () => {
                try {
                    set({ isLoading: true, error: null });
                    const users = await userService.getAllUsers();
                    set({ users, isLoading: false });
                } catch (error: any) {
                    set({ error: error.message, isLoading: false });
                }
            },

            fetchRoles: async () => {
                try {
                    set({ isLoading: true, error: null });
                    const roles = await userService.getRoles();
                    set({ roles, isLoading: false });
                } catch (error: any) {
                    set({ error: error.message, isLoading: false });
                }
            },

            updateUserRoles: async (userId: string, roleIds: string[]) => {
                try {
                    set({ isLoading: true, error: null });
                    await userService.updateUserRoles(userId, roleIds);
                    await get().fetchUsers();
                } catch (error: any) {
                    set({ error: error.message, isLoading: false });
                    throw error;
                }
            },

            createUser: async (email: string, fullName: string, roleIds: string[] = []) => {
                try {
                    set({ isLoading: true, error: null });
                    const user = await userService.createUser(email, fullName, roleIds);
                    await get().fetchUsers();
                    set({ isLoading: false });
                    return user;
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
            }),
            onRehydrateStorage: () => (state) => {
                state?.setHydrated(true);
            },
        }
    )
);
