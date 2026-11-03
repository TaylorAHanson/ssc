import type { Request } from '../../types';

// Shapes of the OmniGuard (enforcement_sentinel) run payload, as written by
// backend/app/workflows/sentinel.py into the request's state_context and
// served as `Request.metadata`. Records are loosely-typed JSON from older runs
// too, so every field the UI tolerates being absent is optional.

/** `ws_tag` on newer records: the workspace a record came from. */
export interface SentinelWorkspaceTag {
    name?: string;
    host?: string;
    environment?: string;
    type?: string;
}

/** The discovered resource as kept in a check's snapshot or the OPA input. */
export interface SentinelResource {
    id?: string;
    name?: string;
    title?: string;
    display_name?: string;
    owner?: string | null;
    tags?: string[] | Record<string, string | number>;
}

/** The OPA input snapshot stored on each violation record. */
export interface SentinelInputContext {
    workspace?: SentinelWorkspaceTag;
    resource?: SentinelResource;
}

/** A violation record (also carries CERTIFY / SKIPPED_ALLOWLIST rows). */
export interface SentinelViolation {
    resource_id: string;
    resource_type: string;
    owner?: string | null;
    workspace?: SentinelWorkspaceTag;
    policy: string;
    action: string;
    reason?: string;
    violation_reasons?: string[];
    severity: string;
    input_context?: SentinelInputContext;
    resource?: SentinelResource;
}

/** One policy rule's outcome inside a check. */
export interface SentinelRuleResult {
    id?: string;
    description?: string;
    category?: string;
    passed: boolean;
    messages?: string[];
    violations?: string[];
}

/** One (resource, policy) evaluation, PASS or VIOLATION. */
export interface SentinelCheck {
    resource_id: string;
    resource_type: string;
    resource?: SentinelResource;
    workspace?: SentinelWorkspaceTag;
    policy: string;
    result: string;
    action: string;
    reason?: string;
    violation_reasons?: string[];
    rule_results?: SentinelRuleResult[];
    severity: string;
}

/** A workspace whose discovery failed outright or partially. */
export interface SentinelWorkspaceFailure {
    workspace?: string;
    host?: string;
    credential_source?: string;
    category: string;
    failed?: number;
    attempted?: number;
    example?: string;
    partial?: boolean;
    network_reachable?: boolean;
    oauth_error?: string;
    oauth_error_description?: string;
    oauth_status?: number;
}

/** Run-wide counts; also the `data` of the legacy `discover_completed` fact. */
export interface SentinelScanStats {
    violation_count?: number;
    pass_count?: number;
    total_resources_scanned?: number;
    policies_evaluated?: number;
    total_checks?: number;
    severity_counts?: Record<string, number>;
    exempt_count?: number;
}

export interface SentinelRunContext {
    violations?: SentinelViolation[];
    checks?: SentinelCheck[];
    scan_stats?: SentinelScanStats;
    workspace_failures?: SentinelWorkspaceFailure[];
    scan_error?: string;
    workspaces_scanned?: string[];
    workspace?: string;
}

// Older payloads carried the context under these keys instead of `metadata`.
type SentinelRun = Omit<Request, 'metadata'> & {
    metadata?: SentinelRunContext;
    stateContext?: SentinelRunContext;
    state_context?: SentinelRunContext;
};

export const sentinelRunContext = (run: Request): SentinelRunContext => {
    const r = run as SentinelRun;
    return r.stateContext || r.metadata || r.state_context || {};
};

/** Stats from the `discover_completed` fact, for runs that predate scan_stats. */
export const sentinelDiscoverStats = (run: Request): SentinelScanStats | undefined =>
    run.stateMachine?.states
        ?.flatMap(s => s.facts || [])
        .find(f => f.type === 'discover_completed')?.data as SentinelScanStats | undefined;
