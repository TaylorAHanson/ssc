import { create } from 'zustand';
import type { Request, RequestType, Environment, Approval, ApprovalAction, Delegation, DelegationCreate } from '../types';
import { api, getContent } from '../services/api';

interface BannerData {
  message: string;
  active: boolean;
  type?: 'info' | 'alert' | 'warning' | 'success';
}

interface RequestStore {
  requests: Request[];
  approvals: Approval[];
  delegations: Delegation[];
  bannerMessage: string | null;
  bannerData: BannerData | null;
  addRequest: (type: RequestType, title: string, environment?: Environment, metadata?: Record<string, any>) => Promise<void>;
  deleteRequest: (id: string) => Promise<void>;
  updateRequest: (id: string, updates: Partial<Request>) => Promise<void>;
  setBannerMessage: (message: string | null) => void;
  fetchRequests: () => Promise<void>;
  fetchApprovals: () => Promise<void>;
  fetchDelegations: (delegatorEmail?: string) => Promise<void>;
  addDelegation: (delegation: DelegationCreate) => Promise<void>;
  removeDelegation: (id: string) => Promise<void>;
  processApproval: (action: ApprovalAction) => Promise<void>;
  getPendingApprovalsCount: () => number;
  fetchBannerMessage: () => Promise<void>;
  completeTraining: (requestId: string) => Promise<void>;
}

export const useRequestStore = create<RequestStore>((set, get) => ({
  requests: [],
  approvals: [],
  delegations: [],
  bannerMessage: null,
  bannerData: null,

  addRequest: async (type, title, environment, metadata) => {
    try {
      await api.createRequest(type, title, environment, metadata);
      // Removed blocking await fetchRequests()
      get().fetchRequests();
    } catch (error) {
      console.error('Failed to create request:', error);
      throw error;
    }
  },

  deleteRequest: async (id) => {
    try {
      await api.deleteRequest(id);
      // Optimistically update UI
      set((state) => ({
        requests: state.requests.filter(req => req.id !== id)
      }));
      // Also fetch to be sure (non-blocking)
      get().fetchRequests();
    } catch (error) {
      console.error('Failed to delete request:', error);
      throw error;
    }
  },

  updateRequest: async (_id, _updates) => {
    // This is currently client-side only optimistic update support if needed, 
    // but better to just re-fetch or use specific update endpoints if available.
    // Since we don't have a generic PATCH endpoint exposed in the store yet beyond API,
    // we'll keep it simple for now or implement if needed.
    // For now, let's just refresh requests to be safe.
    get().fetchRequests();
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

  fetchDelegations: async (delegatorEmail) => {
    try {
      const delegations = await api.getDelegations(delegatorEmail);
      set({ delegations });
    } catch (error) {
      console.error('Failed to fetch delegations:', error);
    }
  },

  addDelegation: async (delegation) => {
    try {
      await api.createDelegation(delegation);
      await get().fetchDelegations();
    } catch (error) {
      console.error('Failed to add delegation:', error);
      throw error;
    }
  },

  removeDelegation: async (id) => {
    try {
      await api.deleteDelegation(id);
      await get().fetchDelegations();
    } catch (error) {
      console.error('Failed to remove delegation:', error);
      throw error;
    }
  },

  processApproval: async (action) => {
    try {
      if (action.action === 'approve') {
        const approval = get().approvals.find(a => a.id === action.approvalId);
        if (!approval) throw new Error('Approval not found');

        await api.approveRequest(approval.requestId);

      } else if (action.action === 'reject') {
        const approval = get().approvals.find(a => a.id === action.approvalId);
        if (!approval) throw new Error('Approval not found');

        await api.rejectRequest(approval.requestId, action.note);
      }

      // Small delay to allow backend poller to transition state before we refetch
      await new Promise(resolve => setTimeout(resolve, 1500));

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

  completeTraining: async (requestId: string) => {
    try {
      await api.completeTraining(requestId);
      // Wait a moment for the backend poller to pick up the change and update status
      await new Promise(resolve => setTimeout(resolve, 5000));
      // Refresh requests to update UI with completed status
      await get().fetchRequests();
      // Force update of selected request in UI if it exists
      // This is a bit tricky with the current architecture where Requests component holds selected state locally.
      // But fetchRequests updates the global store which the component subscribes to.
    } catch (error) {
      console.error('Failed to complete training:', error);
      throw error;
    }
  },
}));
