/**
 * API service for communicating with the backend.
 */

import type {
  Request, RequestType, Environment, Approval, Delegation, DelegationCreate,
  ReportSubscription, ReportSubscriptionCreate, ReportSubscriptionUpdate, ExecutionSummary
} from '../types';

import { useUserStore } from '../stores/userStore';

// API Base URL - set via VITE_API_BASE_URL environment variable at build time
// For production: /api/v1 (relative)
// For local dev: http://localhost:8000/api/v1
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1';

/**
 * Gets consistent headers including dev role override if active
 */
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
      headers: getHeaders(),
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
  form_schema: Record<string, any>;  // Updated to match backend field name
  schema?: Record<string, any>;  // Legacy field for backward compatibility
}

/**
 * List all available forms.
 */
export async function listForms(): Promise<FormInfo[]> {
  const response = await fetch(`${API_BASE_URL}/admin/forms`, {
    headers: getHeaders()
  });
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
  const response = await fetch(url.toString(), {
    headers: getHeaders()
  });
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
    headers: getHeaders(),
    body: JSON.stringify({
      form_schema: schema,
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
  const response = await fetch(url, {
    headers: getHeaders()
  });
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
  const response = await fetch(`${API_BASE_URL}/content/content`, {
    headers: getHeaders()
  });
  if (!response.ok) {
    throw new Error(`Failed to list content: ${response.statusText}`);
  }
  return response.json();
}

export async function getContent(filename: string, version?: string, params?: Record<string, string>): Promise<Record<string, any> | any[]> {
  const url = new URL(`${API_BASE_URL}/content/content/${filename}`);
  if (version) {
    url.searchParams.set('version', version);
  }
  if (params) {
    Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
  }
  const response = await fetch(url.toString(), {
    headers: getHeaders()
  });
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
    headers: getHeaders(),
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
  const response = await fetch(`${API_BASE_URL}/content/content/${filename}/versions`, {
    headers: getHeaders()
  });
  if (!response.ok) {
    throw new Error(`Failed to get content versions: ${response.statusText}`);
  }
  return response.json();
}

/**
 * GitHub API
 */
export interface GitHubTemplate {
  id: number;
  name: string;
  full_name: string;
  description: string;
  url: string;
  is_template: boolean;
  tags: string[];
  created_at: string;
  updated_at: string;
  owner: string;
}

export async function listGitHubTemplates(): Promise<GitHubTemplate[]> {
  const response = await fetch(`${API_BASE_URL}/github/templates`, {
    headers: getHeaders()
  });
  if (!response.ok) {
    throw new Error(`Failed to list GitHub templates: ${response.statusText}`);
  }
  return response.json();
}

/**
 * Trigger a manual calendar sync.
 */
export async function triggerCalendarSync(): Promise<{ status: string; message: string }> {
  const response = await fetch(`${API_BASE_URL}/content/calendar/sync`, {
    method: 'POST',
    headers: getHeaders()
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to sync calendar: ${response.statusText}`);
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
  const response = await fetch(`${API_BASE_URL}/admin/workspaces`, {
    headers: getHeaders()
  });
  if (!response.ok) {
    throw new Error(`Failed to list workspaces: ${response.statusText}`);
  }
  return response.json();
}

/**
 * Get feature states for a specific workspace.
 */
export async function getWorkspaceFeatures(workspaceId: string): Promise<WorkspaceFeaturesResponse> {
  const response = await fetch(`${API_BASE_URL}/admin/workspaces/${workspaceId}/features`, {
    headers: getHeaders()
  });
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
    headers: getHeaders(),
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
    headers: getHeaders(),
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
  const response = await fetch(`${API_BASE_URL}/requests`, {
    headers: getHeaders()
  });
  if (!response.ok) {
    throw new Error(`Failed to get requests: ${response.statusText}`);
  }
  return response.json();
}

export async function getRequest(requestId: string): Promise<Request> {
  const response = await fetch(`${API_BASE_URL}/requests/${requestId}`, {
    headers: getHeaders()
  });
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
    headers: getHeaders(),
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
  const response = await fetch(url.toString(), {
    headers: getHeaders()
  });
  if (!response.ok) {
    throw new Error(`Failed to get approvals: ${response.statusText}`);
  }
  return response.json();
}

export async function deleteRequest(requestId: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/requests/${requestId}`, {
    method: 'DELETE',
  });
  if (!response.ok) {
    throw new Error(`Failed to delete request: ${response.statusText}`);
  }
}

export async function completeTraining(requestId: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/requests/${requestId}/complete-training`, {
    method: 'POST',
  });
  if (!response.ok) {
    throw new Error(`Failed to complete training: ${response.statusText}`);
  }
}

/**
 * Delegation Management API
 */
export async function getDelegations(delegatorEmail?: string, delegateeEmail?: string): Promise<Delegation[]> {
  const url = new URL(`${API_BASE_URL}/delegations`);
  if (delegatorEmail) {
    url.searchParams.set('delegator_email', delegatorEmail);
  }
  if (delegateeEmail) {
    url.searchParams.set('delegatee_email', delegateeEmail);
  }
  const response = await fetch(url.toString(), {
    headers: getHeaders()
  });
  if (!response.ok) {
    throw new Error(`Failed to get delegations: ${response.statusText}`);
  }
  return response.json();
}

export async function createDelegation(delegation: DelegationCreate): Promise<Delegation> {
  const response = await fetch(`${API_BASE_URL}/delegations`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify(delegation),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to create delegation: ${response.statusText}`);
  }
  return response.json();
}

export async function deleteDelegation(delegationId: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/delegations/${delegationId}`, {
    method: 'DELETE',
  });
  if (!response.ok) {
    throw new Error(`Failed to delete delegation: ${response.statusText}`);
  }
}

/**
 * Get branding settings.
 */
export async function getBranding(): Promise<{
  brand_name: string;
  brand_logo_url: string;
  brand_color_primary: string;
  brand_color_secondary: string;
  brand_color_info: string;
  brand_color_alert: string;
  brand_color_warning: string;
  brand_color_success: string;
}> {
  const response = await fetch(`${API_BASE_URL}/branding`, {
    headers: getHeaders()
  });
  if (!response.ok) {
    throw new Error(`Failed to get branding: ${response.statusText}`);
  }
  return response.json();
}

// ... existing imports/exports

export interface TestRunRequest {
  path?: string;
  args?: string[];
}

export interface TestRunResponse {
  exit_code: number;
  stdout: string;
  stderr: string;
  command: string[];
}

export async function runTests(path?: string, args?: string[]): Promise<TestRunResponse> {
  const response = await fetch(`${API_BASE_URL}/dev/tests`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({ path, args }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to run tests: ${response.statusText}`);
  }
  return response.json();
}

export async function listTests(): Promise<string[]> {
  const response = await fetch(`${API_BASE_URL}/dev/tests/list`, {
    headers: getHeaders()
  });
  if (!response.ok) {
    throw new Error(`Failed to list tests: ${response.statusText}`);
  }
  const data = await response.json();
  return data.tests;
}

export async function resetDb(): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/dev/db/reset`, {
    method: 'POST',
  });
  if (!response.ok) {
    throw new Error(`Failed to reset database: ${response.statusText}`);
  }
}


/**
 * Reports API
 */
export async function listSubscriptions(): Promise<ReportSubscription[]> {
  const response = await fetch(`${API_BASE_URL}/reports/subscriptions`, {
    headers: getHeaders()
  });
  if (!response.ok) {
    throw new Error(`Failed to list subscriptions: ${response.statusText}`);
  }
  return response.json();
}

export async function createSubscription(data: ReportSubscriptionCreate): Promise<ReportSubscription> {
  const response = await fetch(`${API_BASE_URL}/reports/subscriptions`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to create subscription: ${response.statusText}`);
  }
  return response.json();
}

export async function updateSubscription(id: string, data: ReportSubscriptionUpdate): Promise<ReportSubscription> {
  const response = await fetch(`${API_BASE_URL}/reports/subscriptions/${id}`, {
    method: 'PUT',
    headers: getHeaders(),
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to update subscription: ${response.statusText}`);
  }
  return response.json();
}

export async function deleteSubscription(id: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/reports/subscriptions/${id}`, {
    method: 'DELETE',
    headers: getHeaders(),
  });
  if (!response.ok) {
    throw new Error(`Failed to delete subscription: ${response.statusText}`);
  }
}

export async function listExecutions(subscriptionId?: string): Promise<ExecutionSummary[]> {
  const url = new URL(`${API_BASE_URL}/reports/executions`);
  if (subscriptionId) {
    url.searchParams.set('subscription_id', subscriptionId);
  }
  const response = await fetch(url.toString(), {
    headers: getHeaders()
  });
  if (!response.ok) {
    throw new Error(`Failed to list executions: ${response.statusText}`);
  }
  return response.json();
}

export async function seedDb(): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/dev/db/seed`, {
    method: 'POST',
  });
  if (!response.ok) {
    throw new Error(`Failed to seed database: ${response.statusText}`);
  }
}

export const api = {
  createRequest,
  getRequests,
  getRequest,
  approveRequest,
  rejectRequest,
  deleteRequest,
  getApprovals,
  completeTraining,
  getDelegations,
  createDelegation,
  deleteDelegation,
  runTests,
  listTests,
  resetDb,
  seedDb
};
