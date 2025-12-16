import { create } from 'zustand';
import type { Request, RequestType, Environment, Approval, ApprovalAction, ApprovalType, StateMachineState } from '../types';
import { mockApi } from '../services/mockApi';
import { getContent } from '../services/api';

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
  addRequest: (type: RequestType, title: string, environment?: Environment) => Promise<void>;
  updateRequest: (id: string, updates: Partial<Request>) => Promise<void>;
  setBannerMessage: (message: string | null) => void;
  fetchRequests: () => Promise<void>;
  fetchApprovals: () => Promise<void>;
  processApproval: (action: ApprovalAction) => Promise<void>;
  getPendingApprovalsCount: () => number;
  fetchBannerMessage: () => Promise<void>;
}

// Mock requests data
const mockRequests: Request[] = [
  {
    id: 'req-1',
    type: 'workspace_provision',
    title: 'New Workspace: Analytics Team Workspace - Production',
    status: 'manager_approval',
    createdAt: new Date(Date.now() - 5 * 24 * 60 * 60 * 1000).toISOString(),
    updatedAt: new Date(Date.now() - 1 * 24 * 60 * 60 * 1000).toISOString(),
    environment: 'prod',
    requiresTraining: true,
    trainingCompleted: true,
    stateMachine: {
      currentState: 'manager_approval',
      parallelPaths: [
        {
          id: 'approval',
          name: 'Approval Path',
          required: true,
          states: [
            { id: 'manager_approval', name: 'Manager Approval', status: 'active', order: 1 },
            { id: 'budget_approval', name: 'Budget Approval', status: 'pending', order: 2 },
          ],
        },
        {
          id: 'training',
          name: 'Training Path',
          required: true,
          states: [
            { id: 'training_pending', name: 'Training Completion', status: 'completed', order: 1 },
          ],
        },
      ],
      completedStates: ['training_pending'],
      activeStates: ['manager_approval'],
    },
  },
  {
    id: 'req-2',
    type: 'catalog_schema_table_access',
    title: 'Data Access: platform_catalog - sales_schema',
    status: 'pending',
    createdAt: new Date(Date.now() - 3 * 24 * 60 * 60 * 1000).toISOString(),
    updatedAt: new Date(Date.now() - 3 * 24 * 60 * 60 * 1000).toISOString(),
    stateMachine: {
      currentState: 'pending',
      parallelPaths: [
        {
          id: 'approval',
          name: 'Approval Path',
          required: true,
          states: [
            { id: 'data_owner_approval', name: 'Data Owner Approval', status: 'pending', order: 1 },
          ],
        },
      ],
      completedStates: [],
      activeStates: ['pending'],
    },
  },
  {
    id: 'req-3',
    type: 'service_principal',
    title: 'Service Principal: ETL Pipeline Service',
    status: 'provisioning',
    createdAt: new Date(Date.now() - 2 * 24 * 60 * 60 * 1000).toISOString(),
    updatedAt: new Date(Date.now() - 1 * 60 * 60 * 1000).toISOString(),
    stateMachine: {
      currentState: 'provisioning',
      parallelPaths: [
        {
          id: 'approval',
          name: 'Approval Path',
          required: true,
          states: [
            { id: 'platform_admin_approval', name: 'Platform Admin Approval', status: 'completed', order: 1 },
          ],
        },
        {
          id: 'provisioning',
          name: 'Provisioning Path',
          required: true,
          states: [
            { id: 'provisioning', name: 'Service Principal Creation', status: 'active', order: 1 },
            { id: 'permissions_setup', name: 'Permissions Setup', status: 'pending', order: 2 },
          ],
        },
      ],
      completedStates: ['platform_admin_approval'],
      activeStates: ['provisioning'],
    },
  },
  {
    id: 'req-4',
    type: 'workspace_access',
    title: 'Workspace Access: ML Research Workspace (Admin)',
    status: 'completed',
    createdAt: new Date(Date.now() - 10 * 24 * 60 * 60 * 1000).toISOString(),
    updatedAt: new Date(Date.now() - 8 * 24 * 60 * 60 * 1000).toISOString(),
    stateMachine: {
      currentState: 'completed',
      parallelPaths: [
        {
          id: 'approval',
          name: 'Approval Path',
          required: true,
          states: [
            { id: 'workspace_owner_approval', name: 'Workspace Owner Approval', status: 'completed', order: 1 },
          ],
        },
        {
          id: 'provisioning',
          name: 'Provisioning Path',
          required: true,
          states: [
            { id: 'access_granted', name: 'Access Granted', status: 'completed', order: 1 },
          ],
        },
      ],
      completedStates: ['workspace_owner_approval', 'access_granted'],
      activeStates: [],
    },
  },
  {
    id: 'req-5',
    type: 'rest_api_access',
    title: 'REST API Access: SQL API - /api/2.0/jobs',
    status: 'pending',
    createdAt: new Date(Date.now() - 1 * 24 * 60 * 60 * 1000).toISOString(),
    updatedAt: new Date(Date.now() - 1 * 24 * 60 * 60 * 1000).toISOString(),
    stateMachine: {
      currentState: 'pending',
      parallelPaths: [
        {
          id: 'approval',
          name: 'Approval Path',
          required: true,
          states: [
            { id: 'security_review', name: 'Security Review', status: 'pending', order: 1 },
            { id: 'api_access_grant', name: 'API Access Grant', status: 'pending', order: 2 },
          ],
        },
      ],
      completedStates: [],
      activeStates: ['pending'],
    },
  },
  {
    id: 'req-6',
    type: 'catalog_schema_table',
    title: 'New Catalog/Schema/Table: analytics_catalog.reports_schema.revenue_table',
    status: 'provisioning',
    createdAt: new Date(Date.now() - 4 * 24 * 60 * 60 * 1000).toISOString(),
    updatedAt: new Date(Date.now() - 2 * 24 * 60 * 60 * 1000).toISOString(),
    stateMachine: {
      currentState: 'provisioning',
      parallelPaths: [
        {
          id: 'approval',
          name: 'Approval Path',
          required: true,
          states: [
            { id: 'data_owner_approval', name: 'Data Owner Approval', status: 'completed', order: 1 },
          ],
        },
        {
          id: 'provisioning',
          name: 'Provisioning Path',
          required: true,
          states: [
            { id: 'catalog_creation', name: 'Catalog Creation', status: 'completed', order: 1 },
            { id: 'schema_creation', name: 'Schema Creation', status: 'completed', order: 2 },
            { id: 'table_creation', name: 'Table Creation', status: 'active', order: 3 },
          ],
        },
      ],
      completedStates: ['data_owner_approval', 'catalog_creation', 'schema_creation'],
      activeStates: ['table_creation'],
    },
  },
  {
    id: 'req-7',
    type: 'batch_data_access',
    title: 'Batch Data Access: Customer Data Lake',
    status: 'training_pending',
    createdAt: new Date(Date.now() - 6 * 24 * 60 * 60 * 1000).toISOString(),
    updatedAt: new Date(Date.now() - 5 * 24 * 60 * 60 * 1000).toISOString(),
    requiresTraining: true,
    trainingCompleted: false,
    stateMachine: {
      currentState: 'training_pending',
      parallelPaths: [
        {
          id: 'approval',
          name: 'Approval Path',
          required: true,
          states: [
            { id: 'data_owner_approval', name: 'Data Owner Approval', status: 'completed', order: 1 },
          ],
        },
        {
          id: 'training',
          name: 'Training Path',
          required: true,
          states: [
            { id: 'training_pending', name: 'Training Completion', status: 'active', order: 1 },
          ],
        },
      ],
      completedStates: ['data_owner_approval'],
      activeStates: ['training_pending'],
    },
  },
];

// Mock approvals data
const mockApprovals: Approval[] = [
  {
    id: 'approval-1',
    requestId: 'req-1',
    requestTitle: 'New Workspace: Analytics Team Workspace - Production',
    requestType: 'workspace_provision',
    approvalType: 'platform_admin',
    requestedBy: 'Sarah Johnson',
    requestedByEmail: 'sarah.johnson@qualcomm.com',
    status: 'pending',
    createdAt: new Date(Date.now() - 2 * 24 * 60 * 60 * 1000).toISOString(),
    updatedAt: new Date(Date.now() - 2 * 24 * 60 * 60 * 1000).toISOString(),
  },
  {
    id: 'approval-2',
    requestId: 'req-2',
    requestTitle: 'Data Access: platform_catalog - sales_schema',
    requestType: 'catalog_schema_table_access',
    approvalType: 'data_owner',
    requestedBy: 'Michael Chen',
    requestedByEmail: 'michael.chen@qualcomm.com',
    status: 'pending',
    createdAt: new Date(Date.now() - 1 * 24 * 60 * 60 * 1000).toISOString(),
    updatedAt: new Date(Date.now() - 1 * 24 * 60 * 60 * 1000).toISOString(),
  },
  {
    id: 'approval-3',
    requestId: 'req-3',
    requestTitle: 'Service Principal: ETL Pipeline Service',
    requestType: 'service_principal',
    approvalType: 'platform_admin',
    requestedBy: 'David Martinez',
    requestedByEmail: 'david.martinez@qualcomm.com',
    status: 'pending',
    createdAt: new Date(Date.now() - 6 * 60 * 60 * 1000).toISOString(),
    updatedAt: new Date(Date.now() - 6 * 60 * 60 * 1000).toISOString(),
  },
  {
    id: 'approval-4',
    requestId: 'req-4',
    requestTitle: 'Data Access: platform_catalog - finance_schema, revenue_tables',
    requestType: 'catalog_schema_table_access',
    approvalType: 'data_owner',
    requestedBy: 'Emily Rodriguez',
    requestedByEmail: 'emily.rodriguez@qualcomm.com',
    status: 'pending',
    createdAt: new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString(),
    updatedAt: new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString(),
  },
  {
    id: 'approval-5',
    requestId: 'req-5',
    requestTitle: 'New Workspace: ML Research Workspace - Development',
    requestType: 'workspace_provision',
    approvalType: 'platform_admin',
    requestedBy: 'James Wilson',
    requestedByEmail: 'james.wilson@qualcomm.com',
    status: 'pending',
    createdAt: new Date(Date.now() - 12 * 60 * 60 * 1000).toISOString(),
    updatedAt: new Date(Date.now() - 12 * 60 * 60 * 1000).toISOString(),
  },
];

export const useRequestStore = create<RequestStore>((set, get) => ({
  requests: mockRequests,
  approvals: mockApprovals,
  bannerMessage: null,
  bannerData: null,

  addRequest: async (type, title, environment) => {
    const request = await mockApi.createRequest(type, title, environment);
    set((state) => ({
      requests: [...state.requests, request],
    }));
    // If request requires approval, create an approval
    if (type === 'workspace_provision') {
      const approval: Approval = {
        id: `approval-${Date.now()}`,
        requestId: request.id,
        requestTitle: request.title,
        requestType: type,
        approvalType: 'platform_admin',
        requestedBy: 'John Doe',
        requestedByEmail: 'john.doe@qualcomm.com',
        status: 'pending',
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
      };
      set((state) => ({
        approvals: [...state.approvals, approval],
      }));
    }
  },

  updateRequest: async (id, updates) => {
    set((state) => ({
      requests: state.requests.map((req) =>
        req.id === id ? { ...req, ...updates, updatedAt: new Date().toISOString() } : req
      ),
    }));
  },

  setBannerMessage: (message) => {
    set({ bannerMessage: message });
  },

  fetchRequests: async () => {
    const requests = await mockApi.getRequests();
    set({ requests });
  },

  fetchApprovals: async () => {
    // Mock: In real app, this would fetch from API
    // For now, we'll use the approvals already in state
  },

  processApproval: async (action) => {
    await delay(1500); // Simulate API call
    
    set((state) => {
      const updatedApprovals = state.approvals.map((approval) => {
        if (approval.id === action.approvalId) {
          const updated: Approval = {
            ...approval,
            status: action.action === 'approve' ? 'approved' : action.action === 'reject' ? 'rejected' : 'delegated',
            updatedAt: new Date().toISOString(),
            rejectionNote: action.action === 'reject' ? action.note : undefined,
            delegatedTo: action.action === 'delegate' ? action.delegatedToEmail : undefined,
            delegatedToEmail: action.action === 'delegate' ? action.delegatedToEmail : undefined,
          };
          
          // Update the corresponding request status
          if (action.action === 'approve') {
            state.requests = state.requests.map((req) =>
              req.id === approval.requestId
                ? { ...req, status: 'provisioning', updatedAt: new Date().toISOString() }
                : req
            );
          } else if (action.action === 'reject') {
            state.requests = state.requests.map((req) =>
              req.id === approval.requestId
                ? { ...req, status: 'rejected', updatedAt: new Date().toISOString() }
                : req
            );
          }
          
          return updated;
        }
        return approval;
      });
      
      return { approvals: updatedApprovals };
    });
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

const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

