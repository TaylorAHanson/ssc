/** Typed client for the Metadata Manager API (ADR-0006), `/api/v1/metadata`. */

import type {
  AdoptRequest,
  ChangePreview,
  ChangeRequest,
  ChangeResult,
  ChangeSet,
  ChangeSetPage,
  ChangeSource,
  ImportRequest,
  KeyInfo,
  MetadataConfig,
  ObjectDetail,
  PrincipalIn,
  PrincipalOut,
  PromoteRequest,
  ReconcileAccepted,
  ReconcileNotify,
  ReconcileResult,
  ReconcileRunPage,
  RevertRequest,
  RunRequest,
  SearchQuery,
  SearchResult,
  VersionConflictItem,
  WhoAmI,
} from '../types/metadata';
import { useUserStore } from '../stores/userStore';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1';
const METADATA_BASE = `${API_BASE_URL}/metadata`;

// --- Errors -----------------------------------------------------------------------------

/** Any non-2xx response; `detail` is FastAPI's `detail` (a string for 400/403/404). */
export class MetadataApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, message: string, detail: unknown) {
    super(message);
    this.name = 'MetadataApiError';
    this.status = status;
    this.detail = detail;
  }
}

/** 409: an expected version no longer matches. */
export class MetadataConflictError extends MetadataApiError {
  conflicts: VersionConflictItem[];

  constructor(message: string, conflicts: VersionConflictItem[], detail: unknown = { message, conflicts }) {
    super(409, message, detail);
    this.name = 'MetadataConflictError';
    this.conflicts = conflicts;
  }
}

/** 422: policy blocked the change; `preview` carries the blocking findings. */
export class MetadataBlockedError extends MetadataApiError {
  preview: ChangePreview;

  constructor(message: string, preview: ChangePreview, detail: unknown = { message, preview }) {
    super(422, message, detail);
    this.name = 'MetadataBlockedError';
    this.preview = preview;
  }
}

function messageOf(detail: unknown, fallback: string): string {
  if (typeof detail === 'string' && detail) return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d: { loc?: unknown[]; msg?: string }) => `${d?.loc?.slice(-1)[0] ?? 'field'}: ${d?.msg ?? ''}`)
      .join('; ');
  }
  if (detail && typeof detail === 'object' && 'message' in detail) return String((detail as { message: unknown }).message);
  return fallback;
}

async function toError(response: Response): Promise<MetadataApiError> {
  const body = await response.json().catch(() => null);
  const detail = body && typeof body === 'object' && 'detail' in body ? body.detail : body;
  const message = messageOf(detail, `Request failed: ${response.status} ${response.statusText}`);
  if (response.status === 409 && detail && typeof detail === 'object' && Array.isArray((detail as { conflicts?: unknown }).conflicts)) {
    return new MetadataConflictError(message, (detail as { conflicts: VersionConflictItem[] }).conflicts, detail);
  }
  if (response.status === 422 && detail && typeof detail === 'object' && 'preview' in detail) {
    return new MetadataBlockedError(message, (detail as { preview: ChangePreview }).preview, detail);
  }
  return new MetadataApiError(response.status, message, detail);
}

// --- Transport ---------------------------------------------------------------------------

function headers(extra?: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = { 'Content-Type': 'application/json' };
  const { isDevMode, activeRoleOverride } = useUserStore.getState();
  if (isDevMode && activeRoleOverride) out['X-Dev-Role-Override'] = activeRoleOverride;
  return { ...out, ...extra };
}

type QueryValue = string | number | boolean | null | undefined | ReadonlyArray<string | number>;

/** Arrays become repeated params (`catalogs=a&catalogs=b`); null/undefined/'' are left out. */
export function buildQuery(params: Record<string, QueryValue>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === '') continue;
    if (Array.isArray(value)) value.forEach((v) => search.append(key, String(v)));
    else search.set(key, String(value));
  }
  const qs = search.toString();
  return qs ? `?${qs}` : '';
}

async function call<T>(path: string, init?: RequestInit & { extraHeaders?: Record<string, string> }): Promise<T> {
  const { extraHeaders, ...rest } = init ?? {};
  const response = await fetch(`${METADATA_BASE}${path}`, { ...rest, headers: headers(extraHeaders) });
  if (!response.ok) throw await toError(response);
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

const post = <T>(path: string, body: unknown, extraHeaders?: Record<string, string>) =>
  call<T>(path, { method: 'POST', body: JSON.stringify(body), extraHeaders });

// --- Identity and config ----------------------------------------------------------------

/** GET /whoami */
export async function getWhoAmI(): Promise<WhoAmI> {
  return call('/whoami');
}

/** GET /config */
export async function getConfig(): Promise<MetadataConfig> {
  return call('/config');
}

// --- Search and detail -------------------------------------------------------------------

/** GET /objects. Tag filters go as repeated `tag` params (`key=value`, `key`, `!key`). */
export async function searchObjects(query: SearchQuery = {}): Promise<SearchResult> {
  const { tags, drift, ...rest } = query;
  return call(`/objects${buildQuery({ ...rest, tag: tags, drift: drift ?? undefined })}`);
}

/** GET /object?name=&column= */
export async function getObject(name: string, column?: string | null): Promise<ObjectDetail> {
  return call(`/object${buildQuery({ name, column })}`);
}

/** GET /keys */
export async function listKeys(params: { q?: string; limit?: number } = {}): Promise<KeyInfo[]> {
  return call(`/keys${buildQuery(params)}`);
}

// --- Changes ----------------------------------------------------------------------------

/** POST /changes/preview */
export async function previewChange(request: ChangeRequest): Promise<ChangePreview> {
  return post('/changes/preview', request);
}

/** POST /changes. `idempotencyKey` travels as the `Idempotency-Key` header. */
export async function submitChange(request: ChangeRequest, opts: { idempotencyKey?: string } = {}): Promise<ChangeResult> {
  return post('/changes', request, opts.idempotencyKey ? { 'Idempotency-Key': opts.idempotencyKey } : undefined);
}

/** POST /changes/promote/preview */
export async function previewPromote(request: PromoteRequest): Promise<ChangePreview> {
  return post('/changes/promote/preview', request);
}

/** POST /changes/promote */
export async function submitPromote(request: PromoteRequest): Promise<ChangeResult> {
  return post('/changes/promote', request);
}

export interface ChangeSetListParams {
  actor?: string;
  source?: ChangeSource | '';
  /** An object's name; a catalog or schema also matches the objects under it. */
  name?: string;
  column?: string;
  page?: number;
  page_size?: number;
}

/** GET /changes */
export async function listChangeSets(params: ChangeSetListParams = {}): Promise<ChangeSetPage> {
  return call(`/changes${buildQuery({ ...params })}`);
}

/** GET /changes/{id} */
export async function getChangeSet(id: string): Promise<ChangeSet> {
  return call(`/changes/${encodeURIComponent(id)}`);
}

/** POST /changes/{id}/revert/preview */
export async function previewRevert(id: string, request: RevertRequest = {}): Promise<ChangePreview> {
  return post(`/changes/${encodeURIComponent(id)}/revert/preview`, request);
}

/** POST /changes/{id}/revert */
export async function submitRevert(id: string, request: RevertRequest = {}): Promise<ChangeResult> {
  return post(`/changes/${encodeURIComponent(id)}/revert`, request);
}

// --- Reconciler ---------------------------------------------------------------------------

/** POST /reconcile */
export async function notifyReconcile(body: ReconcileNotify): Promise<ReconcileAccepted> {
  return post('/reconcile', body);
}

/** GET /drift */
export async function getDrift(params: { catalog?: string; page?: number; page_size?: number } = {}): Promise<SearchResult> {
  return call(`/drift${buildQuery(params)}`);
}

/** GET /runs */
export async function listRuns(params: { page?: number; page_size?: number } = {}): Promise<ReconcileRunPage> {
  return call(`/runs${buildQuery(params)}`);
}

/** GET /runs/{id} */
export async function getRun(id: string): Promise<ReconcileResult> {
  return call(`/runs/${encodeURIComponent(id)}`);
}

/** POST /runs (admin) */
export async function startRun(request: RunRequest): Promise<ReconcileResult> {
  return post('/runs', request);
}

// --- System principals (admin) -------------------------------------------------------

/** GET /principals */
export async function listPrincipals(): Promise<PrincipalOut[]> {
  return call('/principals');
}

/** POST /principals */
export async function createPrincipal(principal: PrincipalIn): Promise<PrincipalOut> {
  return post('/principals', principal);
}

/** PUT /principals/{application_id} */
export async function updatePrincipal(applicationId: string, principal: PrincipalIn): Promise<PrincipalOut> {
  return call(`/principals/${encodeURIComponent(applicationId)}`, { method: 'PUT', body: JSON.stringify(principal) });
}

/** DELETE /principals/{application_id} */
export async function deletePrincipal(applicationId: string): Promise<void> {
  await call<void>(`/principals/${encodeURIComponent(applicationId)}`, { method: 'DELETE' });
}

// --- Adopt and import (admin) -------------------------------------------------------

/** POST /adopt */
export async function adoptState(request: AdoptRequest): Promise<ChangeResult> {
  return post('/adopt', request);
}

/** POST /import/preview */
export async function previewImport(request: ImportRequest): Promise<ChangePreview> {
  return post('/import/preview', request);
}

/** POST /import */
export async function submitImport(request: ImportRequest): Promise<ChangeResult> {
  return post('/import', request);
}

export const metadataApi = {
  getWhoAmI,
  getConfig,
  searchObjects,
  getObject,
  listKeys,
  previewChange,
  submitChange,
  previewPromote,
  submitPromote,
  listChangeSets,
  getChangeSet,
  previewRevert,
  submitRevert,
  notifyReconcile,
  getDrift,
  listRuns,
  getRun,
  startRun,
  listPrincipals,
  createPrincipal,
  updatePrincipal,
  deletePrincipal,
  adoptState,
  previewImport,
  submitImport,
};
