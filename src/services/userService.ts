
import type { User, Role } from '../types';

import { useUserStore } from '../stores/userStore';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1';

// Helper to get headers
function getHeaders(contentType: string = 'application/json'): Record<string, string> {
    const headers: Record<string, string> = {};
    if (contentType) {
        headers['Content-Type'] = contentType;
    }

    // Get current dev mode state from store
    const { isDevMode, activeRoleOverride } = useUserStore.getState();
    if (isDevMode && activeRoleOverride) {
        headers['X-Dev-Role-Override'] = activeRoleOverride;
    }

    return headers;
}

export const userService = {
    async getMe(roleOverride: string | null = null): Promise<User> {
        const headers = getHeaders();
        // Allow explicit override to take precedence (used during role switching)
        if (roleOverride) {
            headers['X-Dev-Role-Override'] = roleOverride;
        }

        const response = await fetch(`${API_BASE_URL}/users/me`, {
            headers,
            cache: 'no-store'
        });
        if (!response.ok) throw new Error('Failed to fetch current user');
        return response.json();
    },

    async getAllUsers(): Promise<User[]> {
        const response = await fetch(`${API_BASE_URL}/users/`, {
            headers: getHeaders()
        });
        if (!response.ok) throw new Error('Failed to fetch users');
        return response.json();
    },

    async getRoles(): Promise<Role[]> {
        const response = await fetch(`${API_BASE_URL}/users/roles`, {
            headers: getHeaders()
        });
        if (!response.ok) throw new Error('Failed to fetch roles');
        return response.json();
    },

    async updateUserRoles(userId: string, roleIds: string[]): Promise<User> {
        const response = await fetch(`${API_BASE_URL}/users/${userId}/roles`, {
            method: 'PUT',
            headers: getHeaders(),
            body: JSON.stringify({ role_ids: roleIds }),
        });
        if (!response.ok) throw new Error('Failed to update user roles');
        return response.json();
    },

    async createUser(email: string, fullName: string, roleIds: string[] = []): Promise<User> {
        const response = await fetch(`${API_BASE_URL}/users/`, {
            method: 'POST',
            headers: getHeaders(),
            body: JSON.stringify({ email, full_name: fullName, role_ids: roleIds }),
        });
        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: 'Failed to create user' }));
            throw new Error(error.detail || 'Failed to create user');
        }
        return response.json();
    }
};
