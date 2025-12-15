import type { Request, RequestType, Environment, StateMachineState } from '../types';

const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

export const mockApi = {
  async createRequest(
    type: RequestType,
    title: string,
    environment?: Environment
  ): Promise<Request> {
    await delay(1500);
    
    const initialState: StateMachineState = {
      currentState: 'pending',
      parallelPaths: [],
      completedStates: [],
      activeStates: ['pending'],
    };

    // Add parallel paths for workspace provisioning
    if (type === 'workspace_provision') {
      initialState.parallelPaths = [
        {
          id: 'approval',
          name: 'Approval Path',
          required: true,
          states: [
            { id: 'manager_approval', name: 'Manager Approval', status: 'pending', order: 1 },
            { id: 'budget_approval', name: 'Budget Approval', status: 'pending', order: 2 },
          ],
        },
        {
          id: 'training',
          name: 'Training Path',
          required: true,
          states: [
            { id: 'training_pending', name: 'Training Completion', status: 'pending', order: 1 },
          ],
        },
      ];
    }

    const request: Request = {
      id: `req-${Date.now()}`,
      type,
      title,
      status: 'pending',
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      stateMachine: initialState,
      requiresTraining: type === 'workspace_provision',
      trainingCompleted: false,
      environment,
    };

    return request;
  },

  async updateRequestStatus(
    requestId: string,
    newStatus: string,
    stateUpdates?: Partial<StateMachineState>
  ): Promise<Request> {
    await delay(1500);
    
    // This would normally fetch from backend
    // For now, we'll return a mock update
    const defaultState: StateMachineState = {
      currentState: newStatus,
      parallelPaths: [],
      completedStates: [],
      activeStates: [newStatus],
    };
    
    return {
      id: requestId,
      type: 'workspace_provision',
      title: 'Mock Request',
      status: newStatus as any,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      stateMachine: stateUpdates ? { ...defaultState, ...stateUpdates } : defaultState,
    };
  },

  async getRequests(): Promise<Request[]> {
    await delay(1500);
    return [];
  },

  async completeTraining(requestId: string): Promise<Request> {
    await delay(1500);
    // Mock implementation
    return {
      id: requestId,
      type: 'workspace_provision',
      title: 'Mock Request',
      status: 'pending',
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      stateMachine: {
        currentState: 'training_pending',
        parallelPaths: [],
        completedStates: ['training_pending'],
        activeStates: [],
      },
      requiresTraining: true,
      trainingCompleted: true,
    };
  },
};

