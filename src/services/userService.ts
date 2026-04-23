
import type { User, RoleMapping } from '../types';

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

        const response = await fetch(`${API_BASE_URL}/roles/me`, {
            headers,
            cache: 'no-store'
        });
        if (!response.ok) throw new Error('Failed to fetch current user');
        return response.json();
    },

    async getRoleMappings(): Promise<RoleMapping[]> {
        const response = await fetch(`${API_BASE_URL}/roles/mapping`, {
            headers: getHeaders()
        });
        if (!response.ok) throw new Error('Failed to fetch role mappings');
        return response.json();
    },

    async createRoleMapping(externalRole: string, internalRole: string): Promise<RoleMapping> {
        const response = await fetch(`${API_BASE_URL}/roles/mapping`, {
            method: 'POST',
            headers: getHeaders(),
            body: JSON.stringify({ external_role: externalRole, internal_role: internalRole }),
        });
        if (!response.ok) throw new Error('Failed to create role mapping');
        return response.json();
    },

    async updateRoleMapping(id: number, externalRole: string, internalRole: string): Promise<RoleMapping> {
        const response = await fetch(`${API_BASE_URL}/roles/mapping/${id}`, {
            method: 'PUT',
            headers: getHeaders(),
            body: JSON.stringify({ external_role: externalRole, internal_role: internalRole }),
        });
        if (!response.ok) throw new Error('Failed to update role mapping');
        return response.json();
    },

    async deleteRoleMapping(id: number): Promise<void> {
        const response = await fetch(`${API_BASE_URL}/roles/mapping/${id}`, {
            method: 'DELETE',
            headers: getHeaders()
        });
        if (!response.ok) throw new Error('Failed to delete role mapping');
    }
};
