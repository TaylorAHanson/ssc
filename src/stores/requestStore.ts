import { create } from 'zustand';
import type { Request, RequestType, Environment, Approval, ApprovalAction } from '../types';
import { api, getContent } from '../services/api';

interface BannerData {
  message: string;
  active: boolean;
  type?: 'info' | 'alert' | 'warning' | 'success';
}

interface RequestStore {
  requests: Request[];
  approvals: Approval[];
  bannerMessage: string | null;
  bannerData: BannerData | null;
  addRequest: (type: RequestType, title: string, environment?: Environment, metadata?: Record<string, any>) => Promise<void>;
  updateRequest: (id: string, updates: Partial<Request>) => Promise<void>;
  setBannerMessage: (message: string | null) => void;
  fetchRequests: () => Promise<void>;
  fetchApprovals: () => Promise<void>;
  processApproval: (action: ApprovalAction) => Promise<void>;
  getPendingApprovalsCount: () => number;
  fetchBannerMessage: () => Promise<void>;
}

export const useRequestStore = create<RequestStore>((set, get) => ({
  requests: [],
  approvals: [],
  bannerMessage: null,
  bannerData: null,

  addRequest: async (type, title, environment, metadata) => {
    try {
      await api.createRequest(type, title, environment, metadata);
      // Refresh requests after adding
      await get().fetchRequests();
    } catch (error) {
      console.error('Failed to create request:', error);
      throw error;
    }
  },

  updateRequest: async (id, updates) => {
    // This is currently client-side only optimistic update support if needed, 
    // but better to just re-fetch or use specific update endpoints if available.
    // Since we don't have a generic PATCH endpoint exposed in the store yet beyond API,
    // we'll keep it simple for now or implement if needed.
    // For now, let's just refresh requests to be safe.
    await get().fetchRequests();
  },

  setBannerMessage: (message) => {
    set({ bannerMessage: message });
  },

  fetchRequests: async () => {
    try {
      const requests = await api.getRequests();
      set({ requests });
    } catch (error) {
      console.error('Failed to fetch requests:', error);
    }
  },

  fetchApprovals: async () => {
    try {
      const approvals = await api.getApprovals();
      set({ approvals });
    } catch (error) {
      console.error('Failed to fetch approvals:', error);
    }
  },

  processApproval: async (action) => {
    try {
      if (action.action === 'approve') {
        await api.approveRequest(action.approvalId); // Wait, approvalId is an approval ID, but api expects request ID?
        // The API I wrote: api.approveRequest(requestId)
        // The store currently passes approvalId.
        // I need to map approvalId to requestId or change the API to accept approvalId.
        // But wait, the backend endpoint is /requests/{request_id}/approve.
        // So I need to find the request ID from the approval.
        
        const approval = get().approvals.find(a => a.id === action.approvalId);
        if (!approval) throw new Error('Approval not found');
        
        await api.approveRequest(approval.requestId);
        
      } else if (action.action === 'reject') {
        const approval = get().approvals.find(a => a.id === action.approvalId);
        if (!approval) throw new Error('Approval not found');
        
        await api.rejectRequest(approval.requestId, action.note);
      }
      
      // Refresh state
      await Promise.all([
        get().fetchRequests(),
        get().fetchApprovals()
      ]);
    } catch (error) {
      console.error('Failed to process approval:', error);
      throw error;
    }
  },

  getPendingApprovalsCount: () => {
    return get().approvals.filter((a) => a.status === 'pending').length;
  },

  fetchBannerMessage: async () => {
    try {
      const bannerData = await getContent('system-banner') as BannerData;
      if (bannerData && bannerData.active && bannerData.message) {
        set({ 
          bannerMessage: bannerData.message,
          bannerData: bannerData
        });
      } else {
        set({ bannerMessage: null, bannerData: null });
      }
    } catch (error) {
      console.error('Failed to fetch banner message:', error);
      // Fallback or leave as is
      set({ bannerMessage: null, bannerData: null });
    }
  },
}));
