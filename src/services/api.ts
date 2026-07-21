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
  const url = new URL(`${API_BASE_URL}/admin/forms${formPath}`, window.location.origin);
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
 * Content API (read-only). Content files (events, community links, training)
 * are edited in-repo / synced; the app only reads them here. `getContent` backs
 * the Events page and any other consumers.
 */
export async function getContent(filename: string, version?: string, params?: Record<string, string>): Promise<Record<string, any> | any[]> {
  const url = new URL(`${API_BASE_URL}/content/content/${filename}`, window.location.origin);
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

export async function getPaginatedRequests(params: { skip: number, limit: number, type?: string, search?: string, summary?: boolean }): Promise<{ items: Request[], total: number }> {
  const url = new URL(`${API_BASE_URL}/requests/paginated`, window.location.origin);
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined) {
      url.searchParams.append(k, v.toString());
    }
  });
  const response = await fetch(url.toString(), {
    headers: getHeaders()
  });
  if (!response.ok) {
    throw new Error(`Failed to get paginated requests: ${response.statusText}`);
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

export interface EnforcementActionRecord {
  resource_id: string;
  resource_type: string;
  workspace?: string | null;
  policy_name: string;
  action: string;
  executed_action: string;
  reason?: string | null;
  at: string | null;
}

// Manual enforcement actions recorded for a Sentinel run, used to durably
// rehydrate the "Executed" state after a page refresh.
export async function getEnforcementActions(requestId: string): Promise<EnforcementActionRecord[]> {
  const response = await fetch(`${API_BASE_URL}/requests/${requestId}/enforcement-actions`, {
    headers: getHeaders()
  });
  if (!response.ok) {
    throw new Error(`Failed to get enforcement actions: ${response.statusText}`);
  }
  return response.json();
}

export type GraphRunState = 'done' | 'current' | 'pending' | 'rejected';

export interface RequestGraph {
  request_id: string;
  request_type: string;
  status: string;
  current: string;
  graph_spec: WorkflowGraphSpec;
  node_states: Record<string, GraphRunState>;
}

export async function getRequestGraph(requestId: string): Promise<RequestGraph> {
  const response = await fetch(`${API_BASE_URL}/requests/${requestId}/graph`, {
    headers: getHeaders(),
  });
  if (!response.ok) {
    throw new Error(`Failed to get request graph: ${response.statusText}`);
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
  const url = new URL(`${API_BASE_URL}/approvals`, window.location.origin);
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
  const url = new URL(`${API_BASE_URL}/delegations`, window.location.origin);
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
 * One iframe-embedded app, as served by GET /branding. Driven from the
 * `embedded_apps:` list in configuration.yaml, in list order.
 */
export interface EmbeddedAppConfig {
  id: string;
  title: string;
  url: string;
  icon?: string;
  group?: string;
  description?: string;
  allowed_personas?: string[];
}

/**
 * Self-Service Center catalog config (GET /branding › self_service_center).
 * Drives the alternate landing view: categories of quick-action cards that
 * either seed the Assistant (`prompt`) or navigate to a route (`route`).
 */
export interface SelfServiceCenterCard {
  title: string;
  description?: string;
  prompt?: string;
  route?: string;
  icon?: string;
  allowed_personas?: string[];
}
export interface SelfServiceCenterCategory {
  title: string;
  icon?: string;
  cards?: SelfServiceCenterCard[];
}
export interface SelfServiceCenterConfig {
  enabled?: boolean;
  categories?: SelfServiceCenterCategory[];
}

/**
 * Community Links page config (GET /branding › community_links). Categories of
 * external resource cards, fully curated per customer in configuration.yaml.
 */
export interface CommunityLinkItem {
  title: string;
  url: string;
  description?: string;
  icon?: string;
}
export interface CommunityLinkCategory {
  id?: string;
  name: string;
  icon?: string;
  description?: string;
  // Each link is either the full object form or a compact shorthand string
  // "Title | URL | icon | description" (icon/description optional).
  links?: (CommunityLinkItem | string)[];
}
export interface CommunityLinksConfig {
  enabled?: boolean;
  categories?: CommunityLinkCategory[];
}

/**
 * Get branding settings.
 */
export async function getBranding(): Promise<{
  brand_name: string;
  brand_short_name?: string;
  brand_logo_url: string;
  brand_color_primary: string;
  brand_color_secondary: string;
  brand_color_info: string;
  brand_color_alert: string;
  brand_color_warning: string;
  brand_color_success: string;
  databricks_workspace_url?: string;
  embedded_apps?: EmbeddedAppConfig[];
  genie_full_experience_url?: string;
  features?: Record<string, boolean>;
  tools?: Record<string, boolean>;
  ui?: {
    tabs?: Record<string, boolean>;
  };
  self_service_center?: SelfServiceCenterConfig;
  community_links?: CommunityLinksConfig;
  workflow_authoring_locked?: boolean;
  system_banner?: { active?: boolean; type?: 'info' | 'alert' | 'warning' | 'success'; message?: string };
  dev_features_enabled?: boolean;
}> {
  const response = await fetch(`${API_BASE_URL}/branding`, {
    headers: getHeaders()
  });
  if (!response.ok) {
    throw new Error(`Failed to get branding: ${response.statusText}`);
  }
  return response.json();
}

// ---------------------------------------------------------------------------
// Admin runtime settings (change-on-the-fly configuration overrides)
// ---------------------------------------------------------------------------

/** A single row in a collection setting (e.g. one target workspace). */
export type CollectionRow = Record<string, string | number | boolean | null>;

/** Column spec for a collection (list-of-rows) setting. */
export interface SettingColumn {
  key: string;
  label: string;
  type: 'string' | 'int' | 'bool';
  required?: boolean;
  placeholder?: string;
  help?: string;
}

// --- Catalog value shapes (type === 'catalog') -----------------------------
export interface SelfServiceCard {
  title: string;
  description?: string;
  prompt?: string;
  route?: string;
  allowed_personas?: string[];
}
export interface SelfServiceCategory {
  title: string;
  icon?: string;
  cards: SelfServiceCard[];
}
export interface SelfServiceCatalog {
  enabled: boolean;
  categories: SelfServiceCategory[];
}

export interface CommunityLink {
  title: string;
  url: string;
  icon?: string;
  description?: string;
}
export interface CommunityCategory {
  name: string;
  icon?: string;
  links: CommunityLink[];
}
export interface CommunityLinksCatalog {
  enabled: boolean;
  categories: CommunityCategory[];
}

export interface EmbeddedApp {
  id: string;
  title: string;
  url: string;
  icon?: string;
  group?: string;
  description?: string;
  allowed_personas?: string[];
}

export type CatalogValue = SelfServiceCatalog | CommunityLinksCatalog | EmbeddedApp[];

/** Any value a setting can hold when read or written. */
export type SettingWriteValue =
  | boolean
  | number
  | string
  | CollectionRow[]
  | string[]
  | CatalogValue;

export interface SettingField {
  group: string;
  key: string;
  label: string;
  type: 'bool' | 'int' | 'string' | 'color' | 'select' | 'textarea' | 'collection' | 'string_list' | 'catalog' | 'cron';
  help?: string;
  min?: number;
  max?: number;
  options?: string[];
  // Present when type === 'collection'.
  columns?: SettingColumn[];
  add_label?: string;
  // Present when type === 'catalog': which visual editor to render.
  kind?: 'self_service' | 'community_links' | 'embedded_apps';
  value: SettingWriteValue | null;
}

export interface ReadonlySettingField {
  group: string;
  key: string;
  label: string;
  value: boolean | number | string | null;
}

export interface SettingsState {
  fields: SettingField[];
  readonly: ReadonlySettingField[];
  group_order: string[];
  group_descriptions?: Record<string, string>;
}

/** Get the editable settings spec + current values (Platform Admin only). */
export async function getSettings(): Promise<SettingsState> {
  const response = await fetch(`${API_BASE_URL}/settings`, { headers: getHeaders() });
  if (!response.ok) {
    throw new Error(`Failed to get settings: ${response.statusText}`);
  }
  return response.json();
}

/** Apply + persist a batch of setting overrides. Takes effect immediately. */
export async function updateSettings(
  changes: Record<string, SettingWriteValue>
): Promise<SettingsState> {
  const response = await fetch(`${API_BASE_URL}/settings`, {
    method: 'PUT',
    headers: getHeaders(),
    body: JSON.stringify({ changes }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to update settings: ${response.statusText}`);
  }
  return response.json();
}

// ---------------------------------------------------------------------------
// Governance — on-demand Enforcement Sentinel digest
// ---------------------------------------------------------------------------

export interface DigestInfo {
  hour: number;
  timezone: string;
  label: string;
  next_run: string;
  default_recipient: string;
  latest_run_id: string | null;
  latest_run_at: string | null;
  active_violations: number;
}

/** Schedule + default recipient + latest-run summary for the digest modal. */
export async function getDigestInfo(): Promise<DigestInfo> {
  const response = await fetch(`${API_BASE_URL}/governance/digest-info`, { headers: getHeaders() });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to load digest info: ${response.statusText}`);
  }
  return response.json();
}

/** Send the current digest now to one or more (comma-separated) addresses. */
export async function sendDigestNow(
  email: string
): Promise<{ sent: boolean; recipient: string; violation_count: number; source_run_id: string }> {
  const response = await fetch(`${API_BASE_URL}/governance/digest/send`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({ email }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to send digest: ${response.statusText}`);
  }
  return response.json();
}

/** Delete old Sentinel runs, keeping the most recent `keepLast` terminal runs. */
export async function purgeSentinelRuns(
  keepLast: number
): Promise<{ deleted: number; kept: number; requested_keep_last: number }> {
  const response = await fetch(`${API_BASE_URL}/governance/sentinel/runs/purge`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({ keep_last: keepLast }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to purge Sentinel runs: ${response.statusText}`);
  }
  return response.json();
}

export interface TargetWorkspace {
  name: string;
  environment: string;
  host: string;
}

export async function getTargetWorkspaces(): Promise<{
  workspaces: TargetWorkspace[];
  data_certification_workspace: string;
}> {
  const response = await fetch(`${API_BASE_URL}/governance/target-workspaces`, {
    headers: getHeaders(),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to load target workspaces: ${response.statusText}`);
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
  const url = new URL(`${API_BASE_URL}/reports/executions`, window.location.origin);
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

// Training API
export async function getTrainingStatus(): Promise<{ tracks: any, completed_codes: string[] }> {
  const response = await fetch(`${API_BASE_URL}/training/me`, {
    headers: getHeaders()
  });
  if (!response.ok) {
    throw new Error(`Failed to get training status: ${response.statusText}`);
  }
  return response.json();
}

export interface TrainingCourse {
  code: string;
  name: string;
}

export async function listTrainingCourses(): Promise<TrainingCourse[]> {
  const response = await fetch(`${API_BASE_URL}/training/courses`, {
    headers: getHeaders()
  });
  if (!response.ok) {
    throw new Error(`Failed to list training courses: ${response.statusText}`);
  }
  return response.json();
}

export async function uploadTrainingData(file: File): Promise<{ message: string, stats: any }> {
  const formData = new FormData();
  formData.append('file', file);

  const response = await fetch(`${API_BASE_URL}/training/upload`, {
    method: 'POST',
    headers: {
      'Authorization': getHeaders()['Authorization'] || '',
      'X-Dev-Role-Override': getHeaders()['X-Dev-Role-Override'] || '',
    },
    body: formData,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to upload training data: ${response.statusText}`);
  }
  return response.json();
}

// --- Training LMS (tracks, courses, media, consumption) ---

export interface TrainingConsumption {
  media_id: string;
  course_id: string;
  position_seconds: number;
  percent_complete: number;
  completed: boolean;
  view_count: number;
  last_viewed_at?: string | null;
}

export interface TrainingMedia {
  id: string;
  course_id: string;
  title: string;
  kind: 'video' | 'pdf' | 'slides' | 'doc' | string;
  source_filename?: string | null;
  mime_type?: string | null;
  size_bytes?: number | null;
  duration_seconds?: number | null;
  sort_order: number;
  has_file: boolean;
  created_at?: string | null;
  consumption?: TrainingConsumption | null;
}

export interface TrainingCourseFull {
  id: string;
  track_id: string;
  title: string;
  description?: string | null;
  course_code?: string | null;
  external_url?: string | null;
  section?: string | null;
  course_type?: string | null;
  duration?: string | null;
  unlocks?: string | null;
  source: string;
  sort_order: number;
  status: string;
  media?: TrainingMedia[];
  // Present on the learner /me payload:
  progress?: number;
  status_label?: string;
}

export interface TrainingTrackFull {
  id: string;
  slug: string;
  name: string;
  description?: string | null;
  persona?: string | null;
  icon?: string | null;
  source: string;
  sort_order: number;
  status: string;
  course_count: number;
  courses?: TrainingCourseFull[];
  completed_count?: number;
  total_count?: number;
}

export interface CatalogSyncResult {
  ok: boolean;
  note?: string;
  found: number;
  stats: { added: number; updated: number; skipped: number };
}

export interface CourseConsumptionRow {
  course_id: string;
  course_title: string;
  learners: number;
  avg_percent: number;
  media_completions: number;
}

export function trainingMediaStreamUrl(mediaId: string): string {
  return `${API_BASE_URL}/training/media/${mediaId}/stream`;
}

export async function recordTrainingConsumption(
  mediaId: string,
  positionSeconds: number,
  totalSeconds?: number,
): Promise<TrainingConsumption> {
  const response = await fetch(`${API_BASE_URL}/training/consumption`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({ media_id: mediaId, position_seconds: positionSeconds, total_seconds: totalSeconds }),
  });
  if (!response.ok) {
    throw new Error(`Failed to record consumption: ${response.statusText}`);
  }
  return response.json();
}

// Admin: tracks
export async function adminListTrainingTracks(): Promise<TrainingTrackFull[]> {
  const response = await fetch(`${API_BASE_URL}/training/tracks`, { headers: getHeaders() });
  if (!response.ok) throw new Error(`Failed to list tracks: ${response.statusText}`);
  return response.json();
}

export async function adminCreateTrainingTrack(data: Partial<TrainingTrackFull>): Promise<TrainingTrackFull> {
  const response = await fetch(`${API_BASE_URL}/training/tracks`, {
    method: 'POST', headers: getHeaders(), body: JSON.stringify(data),
  });
  if (!response.ok) {
    const e = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(e.detail || `Failed to create track: ${response.statusText}`);
  }
  return response.json();
}

export async function adminUpdateTrainingTrack(trackId: string, data: Partial<TrainingTrackFull>): Promise<TrainingTrackFull> {
  const response = await fetch(`${API_BASE_URL}/training/tracks/${trackId}`, {
    method: 'PATCH', headers: getHeaders(), body: JSON.stringify(data),
  });
  if (!response.ok) throw new Error(`Failed to update track: ${response.statusText}`);
  return response.json();
}

export async function adminDeleteTrainingTrack(trackId: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/training/tracks/${trackId}`, {
    method: 'DELETE', headers: getHeaders(),
  });
  if (!response.ok) throw new Error(`Failed to delete track: ${response.statusText}`);
}

// Admin: courses
export async function adminCreateTrainingCourse(trackId: string, data: Partial<TrainingCourseFull>): Promise<TrainingCourseFull> {
  const response = await fetch(`${API_BASE_URL}/training/tracks/${trackId}/courses`, {
    method: 'POST', headers: getHeaders(), body: JSON.stringify(data),
  });
  if (!response.ok) {
    const e = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(e.detail || `Failed to create course: ${response.statusText}`);
  }
  return response.json();
}

export async function adminUpdateTrainingCourse(courseId: string, data: Partial<TrainingCourseFull>): Promise<TrainingCourseFull> {
  const response = await fetch(`${API_BASE_URL}/training/courses/${courseId}`, {
    method: 'PATCH', headers: getHeaders(), body: JSON.stringify(data),
  });
  if (!response.ok) throw new Error(`Failed to update course: ${response.statusText}`);
  return response.json();
}

export async function adminDeleteTrainingCourse(courseId: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/training/courses/${courseId}`, {
    method: 'DELETE', headers: getHeaders(),
  });
  if (!response.ok) throw new Error(`Failed to delete course: ${response.statusText}`);
}

// Admin: media
export async function adminUploadTrainingMedia(
  courseId: string, file: File, title: string, kind: string,
): Promise<TrainingMedia> {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('title', title);
  formData.append('kind', kind);
  const response = await fetch(`${API_BASE_URL}/training/courses/${courseId}/media`, {
    method: 'POST',
    headers: {
      'Authorization': getHeaders()['Authorization'] || '',
      'X-Dev-Role-Override': getHeaders()['X-Dev-Role-Override'] || '',
    },
    body: formData,
  });
  if (!response.ok) {
    const e = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(e.detail || `Failed to upload media: ${response.statusText}`);
  }
  return response.json();
}

export async function adminDeleteTrainingMedia(mediaId: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/training/media/${mediaId}`, {
    method: 'DELETE', headers: getHeaders(),
  });
  if (!response.ok) throw new Error(`Failed to delete media: ${response.statusText}`);
}

// Admin: catalog sync + analytics
export async function adminSyncTrainingCatalog(): Promise<CatalogSyncResult> {
  const response = await fetch(`${API_BASE_URL}/training/catalog/sync`, {
    method: 'POST', headers: getHeaders(),
  });
  if (!response.ok) throw new Error(`Failed to sync catalog: ${response.statusText}`);
  return response.json();
}

export async function adminTrainingConsumptionAnalytics(): Promise<CourseConsumptionRow[]> {
  const response = await fetch(`${API_BASE_URL}/training/analytics/consumption`, { headers: getHeaders() });
  if (!response.ok) throw new Error(`Failed to load analytics: ${response.statusText}`);
  return response.json();
}

export async function editRequestParameters(
  requestId: string,
  parameters: Record<string, any>,
  note?: string
): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/requests/${requestId}/edit-parameters`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({ parameters, note }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to edit parameters: ${response.statusText}`);
  }
}

/**
 * Allowlist API
 */
export interface AllowlistEntry {
  id: string;
  resource_id: string;
  resource_type: string;
  workspace: string;
  justification: string;
  status: 'pending' | 'approved' | 'rejected';
  request_id?: string;
  approved_by?: string;
  expires_at?: string;
  created_at: string;
  updated_at: string;
}

export interface AllowlistCreate {
  resource_id: string;
  resource_type: string;
  workspace: string;
  justification: string;
  status?: 'pending' | 'approved' | 'rejected';
  request_id?: string;
  expires_at?: string;
}

export interface AllowlistUpdate {
  justification?: string;
  status?: 'pending' | 'approved' | 'rejected';
  expires_at?: string;
}

export async function getAllowlist(params?: { workspace?: string; resource_type?: string; status?: string }): Promise<AllowlistEntry[]> {
  const url = new URL(`${API_BASE_URL}/allowlist`, window.location.origin);
  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      if (value) url.searchParams.append(key, value);
    });
  }
  
  const response = await fetch(url.toString(), {
    headers: getHeaders()
  });
  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(`Failed to get allowlist: ${response.status} ${errorText}`);
  }
  return response.json();
}

export async function createAllowlistEntry(data: AllowlistCreate): Promise<AllowlistEntry> {
  const response = await fetch(`${API_BASE_URL}/allowlist`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to create allowlist entry: ${response.statusText}`);
  }
  return response.json();
}

export async function updateAllowlistEntry(id: string, data: AllowlistUpdate): Promise<AllowlistEntry> {
  const response = await fetch(`${API_BASE_URL}/allowlist/${id}`, {
    method: 'PUT',
    headers: getHeaders(),
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to update allowlist entry: ${response.statusText}`);
  }
  return response.json();
}

export async function deleteAllowlistEntry(id: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/allowlist/${id}`, {
    method: 'DELETE',
    headers: getHeaders(),
  });
  if (!response.ok) {
    throw new Error(`Failed to delete allowlist entry: ${response.statusText}`);
  }
}

// ---------------------------------------------------------------------------
// Tool Registry (dynamic agent tool governance)
// ---------------------------------------------------------------------------

export interface RegistryTool {
  id: string;
  tool_name: string;
  origin: 'local' | 'workflow' | 'mcp';
  source_id: string | null;
  description: string | null;
  is_mutating: boolean;
  side_effect_class: string;
  enabled: boolean;
  enabled_for_main_agent: boolean;
  enabled_for_workflow_agent: boolean;
  enabled_for_workflow_execution: boolean;
  exposed_via_mcp: boolean;
  allowed_roles: string[];
  identity_mode: 'sp' | 'obo';
  /** Optional $-expression (see backend app/workflows/expr.py) evaluated against
   *  {result} to decide tool success. null = use the default envelope heuristics. */
  success_predicate: unknown | null;
  discovered_at: string | null;
  updated_at: string | null;
}

export interface McpSource {
  id: string;
  name: string;
  server_url: string;
  kind: string;
  enabled: boolean;
  default_identity_mode: 'sp' | 'obo';
  created_by: string | null;
  last_synced_at: string | null;
  last_sync_status: string | null;
  last_sync_error: string | null;
  last_tool_count: number | null;
}

export interface ToolRegistryData {
  tools: RegistryTool[];
  sources: McpSource[];
  source_kinds: string[];
}

export interface RegistryToolUpdate {
  enabled?: boolean;
  enabled_for_main_agent?: boolean;
  enabled_for_workflow_agent?: boolean;
  enabled_for_workflow_execution?: boolean;
  exposed_via_mcp?: boolean;
  allowed_roles?: string[];
  identity_mode?: 'sp' | 'obo';
  is_mutating?: boolean;
  side_effect_class?: string;
  /** Send a $-expression object to set, or null to clear, the success check. */
  success_predicate?: unknown | null;
}

export interface McpSourceCreate {
  name: string;
  server_url: string;
  kind?: string;
  default_identity_mode?: 'sp' | 'obo';
}

export interface McpSourceQuickAdd extends McpSourceCreate {
  /** Enable newly-discovered read-only tools for the main agent immediately. */
  auto_enable_read_only?: boolean;
}

export interface McpQuickAddResult {
  source: McpSource;
  discovery: { ok: boolean; count: number; error: string | null };
  auto_enabled: number;
}

export interface AvailableMcpSource {
  name: string;
  server_url: string;
  kind: string;
  detail?: string;
  already_registered?: boolean;
}

export async function getToolRegistry(): Promise<ToolRegistryData> {
  const response = await fetch(`${API_BASE_URL}/tool-registry`, { headers: getHeaders() });
  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(`Failed to load tool registry: ${response.status} ${errorText}`);
  }
  return response.json();
}

export async function updateRegistryTool(id: string, data: RegistryToolUpdate): Promise<RegistryTool> {
  const response = await fetch(`${API_BASE_URL}/tool-registry/${id}`, {
    method: 'PUT',
    headers: getHeaders(),
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to update tool: ${response.statusText}`);
  }
  return response.json();
}

export async function syncLocalTools(): Promise<{ ok: boolean; inserted: number }> {
  const response = await fetch(`${API_BASE_URL}/tool-registry/sync-local`, {
    method: 'POST',
    headers: getHeaders(),
  });
  if (!response.ok) {
    throw new Error(`Failed to sync local tools: ${response.statusText}`);
  }
  return response.json();
}

export async function createMcpSource(data: McpSourceCreate): Promise<McpSource> {
  const response = await fetch(`${API_BASE_URL}/tool-registry/sources`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to create source: ${response.statusText}`);
  }
  return response.json();
}

export async function getAvailableMcpSources(): Promise<{ sources: AvailableMcpSource[] }> {
  const response = await fetch(`${API_BASE_URL}/tool-registry/sources/available`, { headers: getHeaders() });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to list available sources: ${response.statusText}`);
  }
  return response.json();
}

export async function quickAddMcpSource(data: McpSourceQuickAdd): Promise<McpQuickAddResult> {
  const response = await fetch(`${API_BASE_URL}/tool-registry/sources/quick-add`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to add source: ${response.statusText}`);
  }
  return response.json();
}

export async function updateMcpSource(id: string, data: Partial<McpSourceCreate> & { enabled?: boolean }): Promise<McpSource> {
  const response = await fetch(`${API_BASE_URL}/tool-registry/sources/${id}`, {
    method: 'PUT',
    headers: getHeaders(),
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to update source: ${response.statusText}`);
  }
  return response.json();
}

export async function deleteMcpSource(id: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/tool-registry/sources/${id}`, {
    method: 'DELETE',
    headers: getHeaders(),
  });
  if (!response.ok) {
    throw new Error(`Failed to delete source: ${response.statusText}`);
  }
}

export async function syncMcpSource(id: string): Promise<{ ok: boolean; count: number; error: string | null }> {
  const response = await fetch(`${API_BASE_URL}/tool-registry/sources/${id}/sync`, {
    method: 'POST',
    headers: getHeaders(),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to sync source: ${response.statusText}`);
  }
  return response.json();
}

export interface DataAsset {
  id: string;
  catalog: string;
  schema_name: string;
  table_name: string;
  type: string;
  description: string | null;
  owner: string | null;
  domain: string | null;
  tags: string[];
  certified: boolean;
  contract_url: string | null;
  data_quality: any | null;
  certification_violations: string[] | null;
  sla: string | null;
  created_at: string | null;
  last_synced_at: string;
}

export async function getDataAssets(params?: { domain?: string; certified?: boolean; certification_only?: boolean; limit?: number; offset?: number }): Promise<DataAsset[]> {
  const url = new URL(`${API_BASE_URL}/data-assets`, window.location.origin);
  if (params?.domain) {
    url.searchParams.append('domain', params.domain);
  }
  if (params?.certified !== undefined) {
    url.searchParams.append('certified', String(params.certified));
  }
  if (params?.certification_only !== undefined) {
    url.searchParams.append('certification_only', String(params.certification_only));
  }
  if (params?.limit !== undefined) {
    url.searchParams.append('limit', String(params.limit));
  }
  if (params?.offset !== undefined) {
    url.searchParams.append('offset', String(params.offset));
  }
  
  const response = await fetch(url.toString(), {
    headers: getHeaders()
  });
  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(`Failed to get data assets: ${response.status} ${errorText}`);
  }
  return response.json();
}

export interface AccessibleAssetsResponse {
  /** Whether real entitlement data could be computed (needs OBO + warehouse). */
  available: boolean;
  mode: string;
  accessible_ids: string[];
}

/**
 * Returns the IDs of catalog assets the current user can actually access,
 * computed server-side from Unity Catalog as the user (OBO). When `available`
 * is false the caller should hide the "Accessible to me" filter rather than
 * present a fabricated result.
 */
export async function getAccessibleAssetIds(): Promise<AccessibleAssetsResponse> {
  const response = await fetch(`${API_BASE_URL}/data-assets/accessible`, {
    headers: getHeaders(),
  });
  if (!response.ok) {
    return { available: false, mode: 'unavailable', accessible_ids: [] };
  }
  return response.json();
}

// A single per-rule evaluation from the OPA data_certification policy. Shared by
// the Sentinel run report and the ODCS certification checklist so both render an
// identical view (DRY).
export interface CertificationRuleResult {
  id: string;
  description?: string;
  category?: string;
  passed: boolean;
  messages?: string[];
}

export interface DataContract {
  id: string;
  dataset_id: string;
  yaml_content: string;
  version: number;
  is_active: boolean;
  created_at: string;
  created_by: string | null;
  // Asset fields
  catalog?: string | null;
  schema_name?: string | null;
  table_name?: string | null;
  data_quality?: any | null;
  certification_violations?: string[] | null;
  certification_rule_results?: CertificationRuleResult[] | null;
  certified?: boolean;
  last_synced_at?: string | null;
}

export async function getDataContracts(): Promise<DataContract[]> {
  const url = new URL(`${API_BASE_URL}/data-contracts`, window.location.origin);
  const response = await fetch(url.toString(), {
    headers: getHeaders()
  });
  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(`Failed to get data contracts: ${response.status} ${errorText}`);
  }
  return response.json();
}

export async function getContractHistory(datasetId: string): Promise<DataContract[]> {
  const url = new URL(`${API_BASE_URL}/data-contracts/${datasetId}`, window.location.origin);
  const response = await fetch(url.toString(), {
    headers: getHeaders()
  });
  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(`Failed to get contract history: ${response.status} ${errorText}`);
  }
  return response.json();
}

export async function createDataContract(datasetId: string, yamlContent: string): Promise<DataContract> {
  const response = await fetch(`${API_BASE_URL}/data-contracts`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({ dataset_id: datasetId, yaml_content: yamlContent })
  });
  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(`Failed to create data contract: ${response.status} ${errorText}`);
  }
  return response.json();
}

export async function syncDataContracts(datasetId?: string): Promise<{ status: string, message: string }> {
  const url = datasetId ? `${API_BASE_URL}/data-contracts/sync?dataset_id=${encodeURIComponent(datasetId)}` : `${API_BASE_URL}/data-contracts/sync`;
  const response = await fetch(url, {
    method: 'POST',
    headers: getHeaders()
  });
  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(errorText || `Failed to sync data contracts: ${response.statusText}`);
  }
  return response.json();
}

export async function deleteDataContract(datasetId: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/data-contracts/${datasetId}`, {
    method: 'DELETE',
    headers: getHeaders(),
  });
  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(`Failed to delete data contract: ${response.status} ${errorText}`);
  }
}

// Streams the XLSX certification report and triggers a browser download.
export async function downloadCertificationReport(): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/data-contracts/certification-report`, {
    headers: getHeaders(),
  });
  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(errorText || `Failed to generate report: ${response.statusText}`);
  }
  const blob = await response.blob();
  const disposition = response.headers.get('Content-Disposition') || '';
  const match = disposition.match(/filename="?([^"]+)"?/);
  const filename = match ? match[1] : 'data-certification-report.xlsx';
  const objectUrl = window.URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = objectUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(objectUrl);
}

export interface OdpsDocument {
  id: string;
  name: string;
  yaml_content: string;
  version: number;
  is_active: boolean;
  created_at: string;
  created_by: string | null;
}

export async function getOdpsList(): Promise<OdpsDocument[]> {
  const url = new URL(`${API_BASE_URL}/odps`, window.location.origin);
  const response = await fetch(url.toString(), {
    headers: getHeaders()
  });
  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(`Failed to get ODPS list: ${response.status} ${errorText}`);
  }
  return response.json();
}

export async function draftOdps(datasetIds: string[], openapiUrls: string[], name: string): Promise<{ status: string, yaml_content: string }> {
  const response = await fetch(`${API_BASE_URL}/odps/draft`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({ dataset_ids: datasetIds, openapi_urls: openapiUrls, name })
  });
  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(`Failed to draft ODPS: ${response.status} ${errorText}`);
  }
  return response.json();
}

export async function saveOdps(name: string, yamlContent: string): Promise<OdpsDocument> {
  const response = await fetch(`${API_BASE_URL}/odps`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({ name, yaml_content: yamlContent })
  });
  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(`Failed to save ODPS: ${response.status} ${errorText}`);
  }
  return response.json();
}

export async function getOdpsHistory(odpsId: string): Promise<OdpsDocument[]> {
  const url = new URL(`${API_BASE_URL}/odps/${odpsId}`, window.location.origin);
  const response = await fetch(url.toString(), {
    headers: getHeaders()
  });
  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(`Failed to get ODPS history: ${response.status} ${errorText}`);
  }
  return response.json();
}

export async function deleteOdps(odpsId: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/odps/${odpsId}`, {
    method: 'DELETE',
    headers: getHeaders(),
  });
  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(`Failed to delete ODPS: ${response.status} ${errorText}`);
  }
}

export async function checkPolicy(datasetId: string): Promise<{ status: string, message: string }> {
  const response = await fetch(`${API_BASE_URL}/data-contracts/${datasetId}/check-policy`, {
    method: 'POST',
    headers: getHeaders()
  });
  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(errorText || `Failed to check policy: ${response.statusText}`);
  }
  return response.json();
}

export interface ScheduleInfo {
  cron: string;
  next_run: string | null;
}

export interface SystemSchedules {
  enforcement_sentinel: ScheduleInfo;
  data_asset_sync: ScheduleInfo;
  contract_sync: ScheduleInfo;
  event_sync: ScheduleInfo;
}

export async function getSystemSchedules(): Promise<SystemSchedules> {
  const response = await fetch(`${API_BASE_URL}/system/schedules`, {
    headers: getHeaders()
  });
  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(`Failed to get schedules: ${response.status} ${errorText}`);
  }
  return response.json();
}

export async function getDatabricksDashboards(): Promise<any[]> {
  const response = await fetch(`${API_BASE_URL}/data-assets/databricks/dashboards`, {
    headers: getHeaders()
  });
  if (!response.ok) {
    throw new Error(`Failed to get dashboards: ${response.statusText}`);
  }
  return response.json();
}

export async function getDatabricksJobs(): Promise<any[]> {
  const response = await fetch(`${API_BASE_URL}/data-assets/databricks/jobs`, {
    headers: getHeaders()
  });
  if (!response.ok) {
    throw new Error(`Failed to get jobs: ${response.statusText}`);
  }
  return response.json();
}

export async function getDatabricksApps(): Promise<any[]> {
  const response = await fetch(`${API_BASE_URL}/data-assets/databricks/apps`, {
    headers: getHeaders()
  });
  if (!response.ok) {
    throw new Error(`Failed to get apps: ${response.statusText}`);
  }
  return response.json();
}

export async function getDatabricksGenieSpaces(): Promise<any[]> {
  const response = await fetch(`${API_BASE_URL}/data-assets/databricks/genie_spaces`, {
    headers: getHeaders()
  });
  if (!response.ok) {
    throw new Error(`Failed to get genie spaces: ${response.statusText}`);
  }
  return response.json();
}

export interface LineageNeighbor {
  name: string;
  catalog_name?: string | null;
  schema_name?: string | null;
  table_name?: string | null;
  table_type?: string | null;
}

export type DatabricksMetadataErrorKind = 'not_found' | 'permission_denied' | 'error' | null;

export interface TableLineageResponse {
  table_name: string;
  upstreams: LineageNeighbor[];
  downstreams: LineageNeighbor[];
  error?: string | null;
  error_kind?: DatabricksMetadataErrorKind;
}

/**
 * Fetch immediate (1-hop) upstream/downstream tables for a UC table FQN.
 * Backed by Databricks' lineage-tracking API; returns empty arrays when the
 * table has no recorded lineage or when the call fails.
 */
export async function getTableLineage(tableName: string): Promise<TableLineageResponse> {
  const response = await fetch(
    `${API_BASE_URL}/data-assets/databricks/lineage?table_name=${encodeURIComponent(tableName)}`,
    { headers: getHeaders() }
  );
  if (!response.ok) {
    throw new Error(`Failed to get table lineage: ${response.statusText}`);
  }
  return response.json();
}

export interface TableColumn {
  name: string;
  type?: string | null;
  comment?: string | null;
  nullable?: boolean | null;
  position?: number | null;
}

export interface TableDetailsResponse {
  table_name: string;
  comment?: string | null;
  table_type?: string | null;
  data_source_format?: string | null;
  owner?: string | null;
  created_at?: number | string | null;
  updated_at?: number | string | null;
  columns: TableColumn[];
  tags: Record<string, string | null>;
  error?: string | null;
  error_kind?: DatabricksMetadataErrorKind;
}

/**
 * Fetch full Unity Catalog metadata (columns, comment, owner, tags) for a
 * UC table. Used by the Discover page Schema tab for tables without a
 * data contract.
 */
export async function getTableDetails(tableName: string): Promise<TableDetailsResponse> {
  const response = await fetch(
    `${API_BASE_URL}/data-assets/databricks/table?table_name=${encodeURIComponent(tableName)}`,
    { headers: getHeaders() }
  );
  if (!response.ok) {
    throw new Error(`Failed to get table details: ${response.statusText}`);
  }
  return response.json();
}

// ---------------------------------------------------------------------------
// Governance: Tag Management
// ---------------------------------------------------------------------------

export interface TagDataset {
  dataset_id: string;
  catalog?: string | null;
  schema_name?: string | null;
}

export interface TableTags {
  table: string;
  tags: Record<string, string | null>;
}

export interface DatasetTablesResponse {
  dataset_id: string;
  tables: TableTags[];
  suggested_keys: string[];
  error?: string | null;
}

export interface TagChange {
  id: string;
  title: string;
  dataset_id?: string | null;
  status: string;
  pr_url?: string | null;
  pr_number?: number | null;
  table_count: number;
  created_at: string;
  updated_at: string;
}

export async function getTagDatasets(): Promise<TagDataset[]> {
  const response = await fetch(`${API_BASE_URL}/tags/datasets`, { headers: getHeaders() });
  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(`Failed to get tag datasets: ${response.status} ${errorText}`);
  }
  return response.json();
}

export async function getDatasetTags(datasetId: string): Promise<DatasetTablesResponse> {
  const response = await fetch(
    `${API_BASE_URL}/tags/datasets/${encodeURIComponent(datasetId)}/tables`,
    { headers: getHeaders() }
  );
  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(`Failed to get dataset tags: ${response.status} ${errorText}`);
  }
  return response.json();
}

export async function createTagChange(payload: {
  dataset_id: string;
  dataset_name?: string;
  tables: { table: string; desired_tags: Record<string, string> }[];
  pr_title?: string;
}): Promise<TagChange> {
  const response = await fetch(`${API_BASE_URL}/tags/changes`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(`Failed to submit tag change: ${response.status} ${errorText}`);
  }
  return response.json();
}

export async function listTagChanges(): Promise<TagChange[]> {
  const response = await fetch(`${API_BASE_URL}/tags/changes`, { headers: getHeaders() });
  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(`Failed to list tag changes: ${response.status} ${errorText}`);
  }
  return response.json();
}

// ---------------------------------------------------------------------------
// Context Catalog
// ---------------------------------------------------------------------------

export interface ContextDomain {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  parent_id: string | null;
  domain_type: string;
  primary_owner: string | null;
  secondary_owner: string | null;
  reviewers: string[];
  categories: string[];
  created_by: string | null;
  created_at: string | null;
  updated_at: string | null;
  document_count?: number;
  // Aggregate agent-retrieval count across this domain's documents.
  retrieval_count?: number;
}

export interface ContextDocumentSummary {
  id: string;
  domain_id: string;
  title: string;
  doc_type: string;
  source_filename: string | null;
  source_url: string | null;
  storage_path: string | null;
  status: string;
  tags: string[];
  created_by: string | null;
  created_at: string | null;
  updated_at: string | null;
  preview?: string;
  // Retrieval-usage signal: how many times the agent has retrieved this
  // document, and when it last did. Surfaced as a "Usage" indicator.
  retrieval_count?: number;
  last_retrieved_at?: string | null;
}

export interface ContextDocument extends ContextDocumentSummary {
  body_markdown: string | null;
}

export interface ContextDomainDetail extends ContextDomain {
  documents: ContextDocumentSummary[];
}

export interface ContextDomainInput {
  name: string;
  description?: string;
  parent_id?: string | null;
  domain_type?: string;
  primary_owner?: string;
  secondary_owner?: string;
  reviewers?: string[];
  categories?: string[];
}

export interface ContextDocumentInput {
  title: string;
  body_markdown?: string;
  doc_type?: string;
  source_url?: string;
  status?: string;
  tags?: string[];
}

export interface ContextCatalogBundle {
  format: string;
  exported_at?: string;
  domains: Record<string, unknown>[];
}

export interface ContextImportReport {
  domains: { created: string[]; updated: string[]; skipped: string[] };
  documents: { created: string[]; updated: string[]; skipped: string[] };
  errors: { domain: string | null; error: string }[];
}

export interface ContextSearchResult {
  document_id: string;
  document_title: string;
  doc_type: string;
  source_filename: string | null;
  source_url: string | null;
  domain_id: string;
  domain_name: string;
  domain_slug: string;
  chunk_index: number;
  content: string;
  score: number;
}

export async function listContextDomains(): Promise<ContextDomain[]> {
  const response = await fetch(`${API_BASE_URL}/context/domains`, { headers: getHeaders() });
  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(`Failed to list context domains: ${response.status} ${errorText}`);
  }
  return response.json();
}

export async function getContextDomain(domainId: string): Promise<ContextDomainDetail> {
  const response = await fetch(`${API_BASE_URL}/context/domains/${domainId}`, { headers: getHeaders() });
  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(`Failed to get context domain: ${response.status} ${errorText}`);
  }
  return response.json();
}

export async function createContextDomain(data: ContextDomainInput): Promise<ContextDomain> {
  const response = await fetch(`${API_BASE_URL}/context/domains`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to create context domain: ${response.statusText}`);
  }
  return response.json();
}

export async function updateContextDomain(domainId: string, data: Partial<ContextDomainInput>): Promise<ContextDomain> {
  const response = await fetch(`${API_BASE_URL}/context/domains/${domainId}`, {
    method: 'PUT',
    headers: getHeaders(),
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to update context domain: ${response.statusText}`);
  }
  return response.json();
}

export async function deleteContextDomain(domainId: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/context/domains/${domainId}`, {
    method: 'DELETE',
    headers: getHeaders(),
  });
  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(`Failed to delete context domain: ${response.status} ${errorText}`);
  }
}

export async function getContextDocument(documentId: string): Promise<ContextDocument> {
  const response = await fetch(`${API_BASE_URL}/context/documents/${documentId}`, { headers: getHeaders() });
  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(`Failed to get context document: ${response.status} ${errorText}`);
  }
  return response.json();
}

export async function createContextDocument(domainId: string, data: ContextDocumentInput): Promise<ContextDocument> {
  const response = await fetch(`${API_BASE_URL}/context/domains/${domainId}/documents`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to create context document: ${response.statusText}`);
  }
  return response.json();
}

export async function uploadContextDocument(
  domainId: string,
  file: File,
  title?: string,
  status: string = 'published'
): Promise<ContextDocument> {
  const formData = new FormData();
  formData.append('file', file);
  if (title) formData.append('title', title);
  formData.append('status', status);

  const response = await fetch(`${API_BASE_URL}/context/domains/${domainId}/documents/upload`, {
    method: 'POST',
    headers: {
      'X-Dev-Role-Override': getHeaders()['X-Dev-Role-Override'] || '',
    },
    body: formData,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to upload context document: ${response.statusText}`);
  }
  return response.json();
}

export async function updateContextDocument(
  documentId: string,
  data: Partial<ContextDocumentInput> & { domain_id?: string }
): Promise<ContextDocument> {
  const response = await fetch(`${API_BASE_URL}/context/documents/${documentId}`, {
    method: 'PUT',
    headers: getHeaders(),
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to update context document: ${response.statusText}`);
  }
  return response.json();
}

export async function deleteContextDocument(documentId: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/context/documents/${documentId}`, {
    method: 'DELETE',
    headers: getHeaders(),
  });
  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(`Failed to delete context document: ${response.status} ${errorText}`);
  }
}

export async function searchContextCatalog(
  query: string,
  domainSlug?: string,
  limit?: number
): Promise<{ query: string; results: ContextSearchResult[] }> {
  const response = await fetch(`${API_BASE_URL}/context/search`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({ query, domain_slug: domainSlug, limit }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to search context catalog: ${response.statusText}`);
  }
  return response.json();
}

/** Export context domains + documents as a portable bundle for promotion to
 *  another environment. Pass `domainIds` to export a subtree; omit for all. */
export async function exportContextCatalogBundle(
  opts: { domainIds?: string[]; publishedOnly?: boolean } = {},
): Promise<ContextCatalogBundle> {
  const params = new URLSearchParams();
  if (opts.domainIds?.length) params.set('domain_ids', opts.domainIds.join(','));
  if (opts.publishedOnly) params.set('published_only', 'true');
  const qs = params.toString();
  const response = await fetch(`${API_BASE_URL}/context/export/bundle${qs ? `?${qs}` : ''}`, {
    headers: getHeaders(),
  });
  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(`Failed to export catalog: ${response.status} ${errorText}`);
  }
  return response.json();
}

/** Import a bundle into this environment: upsert domains by slug and documents
 *  by title within a domain. `docStatus` 'keep' preserves each doc's exported
 *  status; 'draft'/'published' forces it. */
export async function importContextCatalogBundle(
  bundle: ContextCatalogBundle,
  opts: { docStatus?: 'keep' | 'draft' | 'published'; overwrite?: boolean } = {},
): Promise<ContextImportReport> {
  const response = await fetch(`${API_BASE_URL}/context/import/bundle`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({
      bundle,
      doc_status: opts.docStatus ?? 'keep',
      overwrite: opts.overwrite ?? true,
    }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to import catalog: ${response.statusText}`);
  }
  return response.json();
}

// --- Onboarding suggestions (pre-prompting) ---

export interface AgentSuggestion {
  label: string;
  prompt: string;
}

export interface AgentSuggestionsResponse {
  suggestions: AgentSuggestion[];
  generated: boolean;
}

/**
 * Fetch a short set of personalized starting prompts for the home page.
 * `recentTopics` are lightweight personalization hints (e.g. the user's most
 * recent chat topics). The backend caps the count and falls back to
 * deterministic role-based prompts if the LLM is unavailable.
 */
export async function getAgentSuggestions(
  recentTopics?: string[]
): Promise<AgentSuggestionsResponse> {
  const response = await fetch(`${API_BASE_URL}/agent/suggestions`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({ recent_topics: recentTopics ?? null }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to fetch suggestions: ${response.statusText}`);
  }
  return response.json();
}

// ---------------------------------------------------------------------------
// Feedback / feature requests / bug reports
// ---------------------------------------------------------------------------

export type FeedbackType = 'bug' | 'feature' | 'feedback';
export type FeedbackStatus = 'open' | 'in_progress' | 'resolved' | 'closed' | 'wont_fix';

export interface FeedbackConsoleEntry {
  level?: string;
  message?: string;
  ts?: string;
}

export interface FeedbackNetworkEntry {
  method?: string;
  url?: string;
  status?: number;
  status_text?: string;
  ts?: string;
}

export interface FeedbackSubmitInput {
  type: FeedbackType;
  title: string;
  description?: string;
  severity?: string;
  source?: 'web' | 'chat';
  page_url?: string;
  user_agent?: string;
  app_version?: string;
  console_logs?: FeedbackConsoleEntry[];
  network_errors?: FeedbackNetworkEntry[];
}

export interface FeedbackItem {
  id: string;
  type: FeedbackType;
  title: string;
  description: string | null;
  severity: string | null;
  status: FeedbackStatus;
  source: string;
  submitted_by: string | null;
  submitted_by_name: string | null;
  page_url: string | null;
  app_version: string | null;
  admin_notes: string | null;
  created_at: string | null;
  updated_at: string | null;
  // Present on list responses (lightweight); detail includes the arrays instead.
  has_diagnostics?: boolean;
  // Present on detail responses.
  user_agent?: string | null;
  console_logs?: FeedbackConsoleEntry[];
  network_errors?: FeedbackNetworkEntry[];
}

export async function submitFeedback(data: FeedbackSubmitInput): Promise<FeedbackItem> {
  const response = await fetch(`${API_BASE_URL}/feedback`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to submit feedback: ${response.statusText}`);
  }
  return response.json();
}

export async function listFeedback(params?: { type?: string; status?: string }): Promise<FeedbackItem[]> {
  const qs = new URLSearchParams();
  if (params?.type) qs.set('type', params.type);
  if (params?.status) qs.set('status', params.status);
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  const response = await fetch(`${API_BASE_URL}/feedback${suffix}`, { headers: getHeaders() });
  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(`Failed to list feedback: ${response.status} ${errorText}`);
  }
  return response.json();
}

export async function getFeedback(feedbackId: string): Promise<FeedbackItem> {
  const response = await fetch(`${API_BASE_URL}/feedback/${feedbackId}`, { headers: getHeaders() });
  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(`Failed to get feedback: ${response.status} ${errorText}`);
  }
  return response.json();
}

export async function updateFeedback(
  feedbackId: string,
  data: { status?: FeedbackStatus; admin_notes?: string; severity?: string },
): Promise<FeedbackItem> {
  const response = await fetch(`${API_BASE_URL}/feedback/${feedbackId}`, {
    method: 'PATCH',
    headers: getHeaders(),
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to update feedback: ${response.statusText}`);
  }
  return response.json();
}

export async function deleteFeedback(feedbackId: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/feedback/${feedbackId}`, {
    method: 'DELETE',
    headers: getHeaders(),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to delete feedback: ${response.statusText}`);
  }
}

// --- Workflows (no-code authoring) ---

/** A JSON expression in the safe spec mini-language (see backend app/workflows/expr.py). */
export type SpecExpr = unknown;

export type GateType =
  | 'manager'
  | 'platform_admin'
  | 'data_owner'
  | 'training'
  | 'pr_merge'
  /** @deprecated The sibling-spawn ("children") model is superseded by compound
   *  workflows (a `subworkflow` stage). Retained only so older specs validate;
   *  not offered when authoring new workflows. */
  | 'children';

/** Declarative approver routing for a human gate. When omitted, the gate uses
 *  its type's built-in routing (e.g. `manager` → the requester's manager). */
export type GateApprover =
  | { source: 'group'; group: string }
  | {
      source: 'approver_group_tag';
      assets_from?: SpecExpr | null;
      fallback_to_owner?: boolean;
    };

export interface WorkflowGateStage {
  kind: 'gate';
  name: string;
  type: GateType;
  waiting_status?: string;
  auto_approve?: SpecExpr | null;
  /** Optional: route this gate's approval to a specific group, or resolve the
   *  group from the data's `approver_group` tag. Only meaningful for human
   *  gate types (manager / platform_admin / data_owner). */
  approver?: GateApprover | null;
  /** For `training` gates: the specific LMS course code this gate requires. When
   *  set, the gate auto-satisfies once the requester completes that course.
   *  `course_name` is optional display copy. */
  course_code?: string | null;
  course_name?: string | null;
}

export interface WorkflowStepStage {
  kind: 'step';
  name: string;
  tool: string;
  approvals?: string[];
  running_status?: string;
  success_fact?: string | null;
  args?: Record<string, SpecExpr>;
  for_each?: SpecExpr | null;
  item_args?: Record<string, SpecExpr>;
  /** Conditional branching: when set, the step runs only if this predicate is
   *  truthy for the request; otherwise it's skipped. Null/absent = always runs. */
  run_if?: SpecExpr | null;
}

/** A nested-workflow ("Call workflow") stage — the building block of a compound
 *  workflow. The referenced workflow runs inline as a subgraph: its gates pause
 *  and resume like native ones, and a rejection inside it rejects the parent. */
export interface WorkflowSubWorkflowStage {
  kind: 'subworkflow';
  name: string;
  /** The key of a published workflow to compose. */
  ref: string;
  /** Optional parent-context -> child-context mapping merged before the child runs. */
  input?: Record<string, SpecExpr>;
  /** Optional context keys this stage declares it contributes. */
  writes_context?: string[];
  running_status?: string;
  /** Conditional composition: when set, the nested workflow runs only if this
   *  predicate is truthy for the request; otherwise the whole subworkflow is
   *  skipped. Null/absent = always runs. The field is `run_if` (not `when`). */
  run_if?: SpecExpr | null;
}

export type WorkflowStage =
  | WorkflowGateStage
  | WorkflowStepStage
  | WorkflowSubWorkflowStage;

export interface WorkflowGraphSpec {
  name: string;
  complete_fact?: string | null;
  completed_status?: string;
  stages: WorkflowStage[];
}

export interface WorkflowTool {
  name: string;
  description: string;
  side_effect_class: string;
  is_mutating: boolean;
  external: boolean;
  args?: string[];
  required_args?: string[];
}

export interface DryRunStage {
  kind: 'gate' | 'step' | 'subworkflow';
  name: string;
  // gate
  type?: string;
  waiting_status?: string;
  can_auto_approve?: boolean;
  decision?: 'auto_approve' | 'requires_approval' | 'run' | 'skip';
  // subworkflow (compound)
  ref?: string;
  running_status?: string;
  input?: Record<string, unknown>;
  // step
  tool?: string;
  is_mutating?: boolean;
  side_effect_class?: string;
  approvals?: string[];
  success_fact?: string | null;
  fan_out?: number;
  calls?: Record<string, unknown>[];
  truncated?: boolean;
  /** Step has a run_if predicate (conditional branching). */
  conditional?: boolean;
  /** Whether the conditional step will run for the previewed sample input. */
  will_run?: boolean;
  // shared
  error?: string;
}

export interface DryRunResult {
  name: string;
  completed_status: string;
  complete_fact: string | null;
  stages: DryRunStage[];
  requires_approval: boolean;
  mutating_steps: number;
  warnings?: string[];
}

export type EvaluationSeverity = 'critical' | 'high' | 'medium' | 'low' | 'info';

export interface EvaluationFinding {
  severity: EvaluationSeverity;
  category: string;
  message: string;
  stage: string | null;
  fix: string;
}

export interface WorkflowEvaluation {
  valid: boolean;
  error?: string;
  risk: { score: number; tier: string };
  quality: { score: number; tier: string };
  findings: EvaluationFinding[];
  summary: {
    stage_count?: number;
    gate_count?: number;
    step_count?: number;
    mutating_steps?: number;
    approval_gates?: string[];
    composes?: string[];
  };
}

export interface WorkflowVersion {
  id: string;
  workflow_id: string;
  workflow_key: string;
  version: number;
  name: string | null;
  goal: string | null;
  request_type: string | null;
  published_by: string | null;
  published_at: string | null;
  has_graph: boolean;
  stage_count: number;
}

export interface WorkflowBundle {
  format: string;
  exported_at?: string;
  workflows: Record<string, unknown>[];
}

export interface ImportReport {
  created: string[];
  updated: string[];
  skipped: string[];
  pruned?: string[];
  errors: { key: string | null; error: string }[];
}

export interface Workflow {
  id: string;
  key: string;
  name: string;
  goal: string | null;
  instructions_markdown?: string | null;
  allowed_tools: string[] | null;
  policy_ref: string | null;
  params_schema: Record<string, unknown> | null;
  graph_spec?: WorkflowGraphSpec | null;
  request_type: string | null;
  status: string;
  /** Operational kill switch: when true the workflow is turned OFF (hidden from
   *  the agent) even if `status === 'published'`. Toggling this stays available
   *  when authoring is locked (prod) and never changes the definition/version. */
  disabled?: boolean;
  version: number;
  source: string;
  created_by: string | null;
  created_at: string | null;
  updated_at: string | null;
  /** Derived: 'compound' if the spec composes another workflow (a subworkflow
   *  stage), else 'atomic'. */
  composition?: 'atomic' | 'compound';
  /** Derived: the workflow keys this one composes, in order. */
  subworkflow_refs?: string[];
  /** Advisory at-a-glance evaluation (risk + quality) attached by the list API.
   *  Null when the workflow has no graph_spec to score. */
  evaluation?: WorkflowListEvaluation | null;
}

export interface WorkflowListEvaluation {
  valid: boolean;
  risk: { score: number; tier: string };
  quality: { score: number; tier: string };
  findings: number;
  top_severity: EvaluationSeverity | null;
}

export interface WorkflowInput {
  key?: string;
  name?: string;
  goal?: string | null;
  instructions_markdown?: string | null;
  allowed_tools?: string[] | null;
  policy_ref?: string | null;
  params_schema?: Record<string, unknown> | null;
  graph_spec?: WorkflowGraphSpec | null;
  request_type?: string | null;
  status?: string;
}

export async function listWorkflows(includeDrafts = true): Promise<Workflow[]> {
  const response = await fetch(`${API_BASE_URL}/workflows?include_drafts=${includeDrafts}`, { headers: getHeaders() });
  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(`Failed to list workflows: ${response.status} ${errorText}`);
  }
  return response.json();
}

export async function getWorkflow(workflowId: string): Promise<Workflow> {
  const response = await fetch(`${API_BASE_URL}/workflows/${workflowId}`, { headers: getHeaders() });
  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(`Failed to get workflow: ${response.status} ${errorText}`);
  }
  return response.json();
}

export async function createWorkflow(data: WorkflowInput): Promise<Workflow> {
  const response = await fetch(`${API_BASE_URL}/workflows`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to create workflow: ${response.statusText}`);
  }
  return response.json();
}

export async function updateWorkflow(workflowId: string, data: WorkflowInput): Promise<Workflow> {
  const response = await fetch(`${API_BASE_URL}/workflows/${workflowId}`, {
    method: 'PUT',
    headers: getHeaders(),
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to update workflow: ${response.statusText}`);
  }
  return response.json();
}

export async function publishWorkflow(workflowId: string): Promise<Workflow> {
  const response = await fetch(`${API_BASE_URL}/workflows/${workflowId}/publish`, {
    method: 'POST',
    headers: getHeaders(),
  });
  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(`Failed to publish workflow: ${response.status} ${errorText}`);
  }
  return response.json();
}

export async function unpublishWorkflow(workflowId: string): Promise<Workflow> {
  const response = await fetch(`${API_BASE_URL}/workflows/${workflowId}/unpublish`, {
    method: 'POST',
    headers: getHeaders(),
  });
  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(`Failed to unpublish workflow: ${response.status} ${errorText}`);
  }
  return response.json();
}

export async function setWorkflowDisabled(workflowId: string, disabled: boolean): Promise<Workflow> {
  const action = disabled ? 'disable' : 'enable';
  const response = await fetch(`${API_BASE_URL}/workflows/${workflowId}/${action}`, {
    method: 'POST',
    headers: getHeaders(),
  });
  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(`Failed to ${action} workflow: ${response.status} ${errorText}`);
  }
  return response.json();
}

export async function deleteWorkflow(workflowId: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/workflows/${workflowId}`, {
    method: 'DELETE',
    headers: getHeaders(),
  });
  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(`Failed to delete workflow: ${response.status} ${errorText}`);
  }
}

/** Author-time validation of a workflow graph_spec. Resolves on valid, throws the
 *  backend's detail message (e.g. "stages[1].tool '...' is not a known V2 tool") otherwise. */
export async function validateSpec(graphSpec: WorkflowGraphSpec): Promise<{ valid: boolean; warnings?: string[] }> {
  const response = await fetch(`${API_BASE_URL}/workflows/validate-spec`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({ graph_spec: graphSpec }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Invalid spec: ${response.statusText}`);
  }
  return response.json();
}

/** The wireable V2 tools (name + side-effect class) for the workflow editor's tool picker. */
export async function listWorkflowTools(): Promise<WorkflowTool[]> {
  const response = await fetch(`${API_BASE_URL}/workflows/meta/tools`, { headers: getHeaders() });
  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(`Failed to list workflow tools: ${response.status} ${errorText}`);
  }
  return response.json();
}

/** Dry-run a draft workflow against a sample request: projects which gates auto-approve
 *  and what arguments each step's tool would receive. No tools run, no DB writes. */
export async function testSpec(
  graphSpec: WorkflowGraphSpec,
  sampleContext: Record<string, unknown>,
): Promise<DryRunResult> {
  const response = await fetch(`${API_BASE_URL}/workflows/test-spec`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({ graph_spec: graphSpec, sample_context: sampleContext }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Dry-run failed: ${response.statusText}`);
  }
  return response.json();
}

/** Advisory evaluation of a workflow graph_spec: risk + quality scores and findings.
 *  Deterministic, side-effect free, and never blocks — purely an authoring signal. */
export async function evaluateSpec(graphSpec: WorkflowGraphSpec): Promise<WorkflowEvaluation> {
  const response = await fetch(`${API_BASE_URL}/workflows/evaluate-spec`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({ graph_spec: graphSpec }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Evaluation failed: ${response.statusText}`);
  }
  return response.json();
}

/** Published-version history for a workflow (newest first). */
export async function listWorkflowVersions(workflowId: string): Promise<WorkflowVersion[]> {
  const response = await fetch(`${API_BASE_URL}/workflows/${workflowId}/versions`, { headers: getHeaders() });
  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(`Failed to load history: ${response.status} ${errorText}`);
  }
  return response.json();
}

/** Restore a prior published version's body into the workflow as a fresh draft. */
export async function rollbackWorkflow(workflowId: string, version: number): Promise<Workflow> {
  const response = await fetch(`${API_BASE_URL}/workflows/${workflowId}/rollback`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({ version }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to roll back: ${response.statusText}`);
  }
  return response.json();
}

/** Export workflows as a portable bundle for promotion to another environment. */
export async function exportWorkflowsBundle(
  opts: { ids?: string[]; publishedOnly?: boolean } = {},
): Promise<WorkflowBundle> {
  const params = new URLSearchParams();
  if (opts.ids?.length) params.set('ids', opts.ids.join(','));
  if (opts.publishedOnly) params.set('published_only', 'true');
  const qs = params.toString();
  const response = await fetch(`${API_BASE_URL}/workflows/export/bundle${qs ? `?${qs}` : ''}`, {
    headers: getHeaders(),
  });
  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(`Failed to export: ${response.status} ${errorText}`);
  }
  return response.json();
}

/** Import a bundle into this environment (upsert by key). Defaults to drafts. */
export async function importWorkflowsBundle(
  bundle: WorkflowBundle,
  opts: { asStatus?: 'draft' | 'published'; overwrite?: boolean; prune?: boolean } = {},
): Promise<ImportReport> {
  const response = await fetch(`${API_BASE_URL}/workflows/import/bundle`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({
      bundle,
      as_status: opts.asStatus ?? 'draft',
      overwrite: opts.overwrite ?? true,
      prune: opts.prune ?? false,
    }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `Failed to import: ${response.statusText}`);
  }
  return response.json();
}

/** Duplicate a workflow into a fresh draft. Implemented client-side via create so it works
 *  without a dedicated endpoint; request_type is cleared so the copy is a safe template. */
export async function cloneWorkflow(workflowId: string): Promise<Workflow> {
  const full = await getWorkflow(workflowId);
  return createWorkflow({
    key: `${full.key}_copy`,
    name: full.name ? `${full.name} (copy)` : `${full.key}_copy`,
    goal: full.goal,
    instructions_markdown: full.instructions_markdown,
    allowed_tools: full.allowed_tools,
    policy_ref: full.policy_ref,
    params_schema: full.params_schema,
    graph_spec: full.graph_spec ?? null,
    request_type: null,
    status: 'draft',
  });
}

export const api = {
  createRequest,
  getRequests,
  getPaginatedRequests,
  getEnforcementActions,
  getRequest,
  getRequestGraph,
  approveRequest,
  rejectRequest,
  deleteRequest,
  getApprovals,
  completeTraining,
  getDelegations,
  createDelegation,
  deleteDelegation,
  editRequestParameters,
  runTests,
  listTests,
  resetDb,
  seedDb,
  getTrainingStatus,
  uploadTrainingData,
  getAllowlist,
  createAllowlistEntry,
  updateAllowlistEntry,
  deleteAllowlistEntry,
  getDataAssets,
  getAccessibleAssetIds,
  getDataContracts,
  getContractHistory,
  createDataContract,
  syncDataContracts,
  deleteDataContract,
  downloadCertificationReport,
  checkPolicy,
  getOdpsList,
  draftOdps,
  saveOdps,
  getOdpsHistory,
  deleteOdps,
  getSystemSchedules,
  getDigestInfo,
  sendDigestNow,
  purgeSentinelRuns,
  getTargetWorkspaces,
  getDatabricksDashboards,
  getDatabricksJobs,
  getDatabricksApps,
  getDatabricksGenieSpaces,
  getTableLineage,
  getTableDetails,
  getTagDatasets,
  getDatasetTags,
  createTagChange,
  listTagChanges,
  listContextDomains,
  getContextDomain,
  createContextDomain,
  updateContextDomain,
  deleteContextDomain,
  getContextDocument,
  createContextDocument,
  uploadContextDocument,
  updateContextDocument,
  deleteContextDocument,
  searchContextCatalog,
  exportContextCatalogBundle,
  importContextCatalogBundle,
  getAgentSuggestions,
  submitFeedback,
  listFeedback,
  getFeedback,
  updateFeedback,
  deleteFeedback,
  listWorkflows,
  getWorkflow,
  createWorkflow,
  updateWorkflow,
  publishWorkflow,
  unpublishWorkflow,
  setWorkflowDisabled,
  deleteWorkflow,
  validateSpec,
  evaluateSpec,
  listWorkflowTools,
  testSpec,
  cloneWorkflow,
  listWorkflowVersions,
  rollbackWorkflow,
  exportWorkflowsBundle,
  importWorkflowsBundle,
  listTrainingCourses,
  trainingMediaStreamUrl,
  recordTrainingConsumption,
  adminListTrainingTracks,
  adminCreateTrainingTrack,
  adminUpdateTrainingTrack,
  adminDeleteTrainingTrack,
  adminCreateTrainingCourse,
  adminUpdateTrainingCourse,
  adminDeleteTrainingCourse,
  adminUploadTrainingMedia,
  adminDeleteTrainingMedia,
  adminSyncTrainingCatalog,
  adminTrainingConsumptionAnalytics,
};
