import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { UserPersona } from '../types';

interface UserStore {
    currentPersona: UserPersona;
    setPersona: (persona: UserPersona) => void;
}

export const useUserStore = create<UserStore>()(
    persist(
        (set) => ({
            currentPersona: 'Power User', // Default to Power User as requested
            setPersona: (persona) => set({ currentPersona: persona }),
        }),
        {
            name: 'user-storage', // Persist to localStorage
        }
    )
);
