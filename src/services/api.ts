/**
 * API service for communicating with the backend.
 */

import type { Request, RequestType, Environment, Approval } from '../types';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1';

export interface ChatMessage {
  id: string;
  type: 'user' | 'agent';
  content: string;
  timestamp: string;
}

export interface FollowUpQuestion {
  id: string;
  question: string;
  type: 'text' | 'radio' | 'multi-select';
  options?: string[];
  required: boolean;
}

export interface ConversationRequest {
  query: string;
  conversation_history?: ChatMessage[];
  context?: Record<string, any>;
}

export interface AgentResponse {
  message: string;
  follow_up_questions?: FollowUpQuestion[];
  form_route?: {
    path: string;
    title: string;
  };
  requires_more_info: boolean;
  form_prefill_data?: Record<string, any>;
}

/**
 * Call the agent conversation endpoint.
 */
export async function callAgent(request: ConversationRequest): Promise<AgentResponse> {
  try {
    const response = await fetch(`${API_BASE_URL}/agent/conversation`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(request),
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: response.statusText }));
      throw new Error(error.detail || `HTTP ${response.status}: ${response.statusText}`);
    }

    return response.json();
  } catch (error) {
    // Re-throw with more context for network errors
    if (error instanceof TypeError && error.message.includes('fetch')) {
      throw new Error(`Failed to connect to backend at ${API_BASE_URL}. Is the server running?`);
    }
    throw error;
  }
}

/**
 * Check agent health.
 */
export async function checkAgentHealth(): Promise<any> {
  const response = await fetch(`${API_BASE_URL}/agent/health`);
  if (!response.ok) {
    throw new Error(`Agent health check failed: ${response.statusText}`);
  }
  return response.json();
}

/**
 * Form management API
 */
export interface FormInfo {
  path: string;
  title: string;
  filename: string;
}

export interface FormVersionInfo {
  filename: string;
  date: string;
  is_active: boolean;
}

export interface FormSchemaResponse {
  path: string;
  schema: Record<string, any>;
}

/**
 * List all available forms.
 */
export async function listForms(): Promise<FormInfo[]> {
  const response = await fetch(`${API_BASE_URL}/admin/forms`);
  if (!response.ok) {
    throw new Error(`Failed to list forms: ${response.statusText}`);
  }
  return response.json();
}

/**
 * Get a specific form schema.
 */
export async function getForm(formPath: string, version?: string): Promise<FormSchemaResponse> {
  const url = new URL(`${API_BASE_URL}/admin/forms${formPath}`);
  if (version) {
    url.searchParams.set('version', version);
  }
  const response = await fetch(url.toString());
  if (!response.ok) {
    throw new Error(`Failed to get form: ${response.statusText}`);
  }
  return response.json();
}

/**
 * Save a form schema.
 */
export async function saveForm(
  formPath: string,
  schema: Record<string, any>,
  createVersion: boolean = true
): Promise<FormSchemaResponse> {
  const response = await fetch(`${API_BASE_URL}/admin/forms${formPath}`, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      schema,
      create_version: createVersion,
    }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to save form: ${response.statusText}`);
  }
  return response.json();
}

/**
 * Get all versions of a form.
 */
export async function getFormVersions(formPath: string): Promise<FormVersionInfo[]> {
  // Ensure path starts with / for consistency
  const normalizedPath = formPath.startsWith('/') ? formPath : `/${formPath}`;
  // FastAPI :path parameter should handle slashes, but we need to ensure the path is correct
  // The path should be: /admin/forms{formPath}/versions
  // where formPath can contain slashes like /paas/request/catalog
  const url = `${API_BASE_URL}/admin/forms${normalizedPath}/versions`;
  console.log('Fetching versions from:', url);
  const response = await fetch(url);
  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    console.error('Failed to get form versions:', response.status, errorText);
    throw new Error(`Failed to get form versions: ${response.status} ${errorText}`);
  }
  return response.json();
}

/**
 * Content Management API
 */
export interface ContentInfo {
  filename: string;
  title: string;
}

export interface ContentVersionInfo {
  filename: string;
  date: string;
  is_active: boolean;
}

export async function listContent(): Promise<ContentInfo[]> {
  const response = await fetch(`${API_BASE_URL}/content/content`);
  if (!response.ok) {
    throw new Error(`Failed to list content: ${response.statusText}`);
  }
  return response.json();
}

export async function getContent(filename: string, version?: string): Promise<Record<string, any> | any[]> {
  const url = new URL(`${API_BASE_URL}/content/content/${filename}`);
  if (version) {
    url.searchParams.set('version', version);
  }
  const response = await fetch(url.toString());
  if (!response.ok) {
    throw new Error(`Failed to get content: ${response.statusText}`);
  }
  return response.json();
}

export async function saveContent(
  filename: string,
  content: Record<string, any> | any[],
  createVersion: boolean = true
): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/content/content/${filename}`, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      content,
      create_version: createVersion,
    }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to save content: ${response.statusText}`);
  }
}

export async function getContentVersions(filename: string): Promise<ContentVersionInfo[]> {
  const response = await fetch(`${API_BASE_URL}/content/content/${filename}/versions`);
  if (!response.ok) {
    throw new Error(`Failed to get content versions: ${response.statusText}`);
  }
  return response.json();
}

/**
 * Workspace Feature Management API
 */
export interface WorkspaceInfo {
  id: string;
  name: string;
  url?: string;
}

export interface FeatureInfo {
  id: string;
  name: string;
  description: string;
  category: 'beta' | 'public_preview';
  enabled: boolean;
}

export interface WorkspaceFeaturesResponse {
  workspace_id: string;
  features: FeatureInfo[];
}

/**
 * List all available workspaces.
 */
export async function listWorkspaces(): Promise<WorkspaceInfo[]> {
  const response = await fetch(`${API_BASE_URL}/admin/workspaces`);
  if (!response.ok) {
    throw new Error(`Failed to list workspaces: ${response.statusText}`);
  }
  return response.json();
}

/**
 * Get feature states for a specific workspace.
 */
export async function getWorkspaceFeatures(workspaceId: string): Promise<WorkspaceFeaturesResponse> {
  const response = await fetch(`${API_BASE_URL}/admin/workspaces/${workspaceId}/features`);
  if (!response.ok) {
    throw new Error(`Failed to get workspace features: ${response.statusText}`);
  }
  return response.json();
}

/**
 * Update a feature state for a specific workspace.
 */
export async function updateWorkspaceFeature(
  workspaceId: string,
  featureId: string,
  enabled: boolean
): Promise<FeatureInfo> {
  const response = await fetch(`${API_BASE_URL}/admin/workspaces/${workspaceId}/features/${featureId}`, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ enabled }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to update workspace feature: ${response.statusText}`);
  }
  return response.json();
}

/**
 * Request Management API
 */
export async function createRequest(
  type: RequestType, 
  title: string, 
  environment?: Environment,
  metadata?: Record<string, any>
): Promise<Request> {
  const response = await fetch(`${API_BASE_URL}/requests`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      type,
      title,
      environment,
      metadata
    }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to create request: ${response.statusText}`);
  }

  // The backend returns { request_id, status, message } for create
  // We might want to fetch the full request details or just construct a basic object
  const result = await response.json();
  
  // Fetch the full request to return consistent object
  return getRequest(result.request_id);
}

export async function getRequests(): Promise<Request[]> {
  const response = await fetch(`${API_BASE_URL}/requests`);
  if (!response.ok) {
    throw new Error(`Failed to get requests: ${response.statusText}`);
  }
  return response.json();
}

export async function getRequest(requestId: string): Promise<Request> {
  const response = await fetch(`${API_BASE_URL}/requests/${requestId}`);
  if (!response.ok) {
    throw new Error(`Failed to get request: ${response.statusText}`);
  }
  return response.json();
}

export async function approveRequest(requestId: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/requests/${requestId}/approve`, {
    method: 'POST',
  });
  if (!response.ok) {
    throw new Error(`Failed to approve request: ${response.statusText}`);
  }
}

export async function rejectRequest(requestId: string, reason?: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/requests/${requestId}/reject`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ rejection_note: reason }),
  });
  if (!response.ok) {
    throw new Error(`Failed to reject request: ${response.statusText}`);
  }
}

export async function getApprovals(status?: string): Promise<Approval[]> {
  const url = new URL(`${API_BASE_URL}/approvals`);
  if (status) {
    url.searchParams.set('status', status);
  }
  const response = await fetch(url.toString());
  if (!response.ok) {
    throw new Error(`Failed to get approvals: ${response.statusText}`);
  }
  return response.json();
}

export const api = {
  createRequest,
  getRequests,
  getRequest,
  approveRequest,
  rejectRequest,
  getApprovals
};
