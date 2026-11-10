/** The `run_sql` tool's result, as rendered by `SqlResultPanel`. */

export interface SqlSourceTable {
    name: string;
    in_catalog: boolean;
    type?: string | null;
    certified?: boolean;
    certification_issues?: string[];
    data_quality?: Record<string, unknown> | null;
    owner?: string | null;
    source_system?: string | null;
    catalog_synced_at?: string | null;
    discover_path?: string | null;
}

export interface SqlResult {
    ok?: boolean;
    columns?: string[];
    rows?: unknown[][];
    row_count?: number;
    truncated?: boolean;
    sql?: string;
    statement_id?: string | null;
    executed_at?: string | null;
    source_tables?: SqlSourceTable[];
}

/** The run_sql payload, when `value` is one (anything else renders nothing). */
export function asSqlResult(value: unknown): SqlResult | null {
    if (!value || typeof value !== 'object') return null;
    const r = value as SqlResult;
    return r.ok === true && typeof r.sql === 'string' ? r : null;
}
