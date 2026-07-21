import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { ShieldAlert, AlertTriangle, Search, CheckCircle2, Loader2, X, FileStack, ShieldCheck, ListChecks, ArrowRight, ChevronLeft, ChevronRight, ChevronDown, ClipboardList, SlidersHorizontal, Mail, Send, Clock } from 'lucide-react';
import { api, type TargetWorkspace } from '../../services/api';
import { CertificationChecklist } from '../../components/admin/CertificationChecklist';
import { useState, useMemo, useEffect, useCallback } from 'react';
import { parseISO } from 'date-fns';

// The backend serializes naive UTC datetimes (no timezone suffix). Treat any
// such string as UTC so it isn't misread as the viewer's local wall-clock time.
const parseUtc = (value: string): Date =>
  parseISO(/Z|[+-]\d{2}:?\d{2}$/.test(value) ? value : `${value}Z`);

// Render a UTC timestamp in US Pacific time with an explicit tz label. Uses the
// America/Los_Angeles zone so the abbreviation auto-switches between PST and PDT
// with daylight saving rather than being hardcoded.
const formatPacific = (value: string, opts?: Intl.DateTimeFormatOptions): string =>
  parseUtc(value).toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'America/Los_Angeles',
    timeZoneName: 'short',
    ...opts,
  });

// How many violation rows to render per "page" in the detail table. A completed
// run can hold tens of thousands of violations; rendering them all at once
// freezes the tab, so we render this many and grow on demand ("Load more").
const VIOLATION_PAGE_SIZE = 50;

const formatReason = (v: any) => {
    if (v.violation_reasons && Array.isArray(v.violation_reasons) && v.violation_reasons.length > 0) {
        if (v.violation_reasons.length === 1) {
            return v.violation_reasons[0];
        }
        return (
            <ul className="list-disc pl-4 space-y-1 text-left">
                {v.violation_reasons.map((reason: string, i: number) => (
                    <li key={i}>{reason}</li>
                ))}
            </ul>
        );
    }
    return v.reason;
};

// Resolve the responsible party for a violation. Newer runs carry a top-level
// `owner`; older ones only have it in the OPA input snapshot.
const resolveOwner = (v: any): string =>
    (v?.owner || v?.input_context?.resource?.owner || '').toString().trim();

// Resolve the workspace a violation came from. Newer (multi-workspace) runs
// carry a top-level `workspace` tag; older ones only have it in the OPA input
// snapshot.
const resolveWorkspace = (v: any): string =>
    (v?.workspace?.name || v?.input_context?.workspace?.name || '').toString().trim();

// Friendly display label for a resource type. The backend calls certified data
// products "data_product"; governance/business users know these as "Dataset".
// Unknown types fall back to their raw value (styling may upper/capitalize it).
const resourceTypeLabel = (type: any): string => {
    const t = (type || '').toString().trim().toLowerCase();
    if (t === 'data_product') return 'Dataset';
    return (type || '').toString();
};

// Render an owner as an email (verbatim) or a service-principal id behind an
// "SP" chip; muted "Unknown" when unresolved.
const OwnerCell = ({ owner }: { owner: string }) => {
    if (!owner) return <span className="text-gray-400">Unknown</span>;
    if (owner.includes('@')) return <span className="text-gray-700 break-all">{owner}</span>;
    return (
        <span className="inline-flex items-center gap-1">
            <span className="text-[9px] font-bold text-gray-600 bg-gray-200 px-1.5 py-0.5 rounded">SP</span>
            <span className="font-mono text-[11px] text-gray-600 break-all">{owner}</span>
        </span>
    );
};

export function EnforcementSentinel() {
    const [isRunning, setIsRunning] = useState(false);
    // Keep the primary surface to a single "Run Scan" action; scan scope lives
    // behind this disclosure.
    const [advancedOpen, setAdvancedOpen] = useState(false);
    const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
    const [activeTab, setActiveTab] = useState<string>('all');
    // Top-level toggle: "Violations" shows only failed checks (legacy view).
    // "Checklist" shows every (resource, policy) evaluation — PASS and
    // VIOLATION — so reviewers can audit what was actually verified.
    const [reportView, setReportView] = useState<'violations' | 'checklist'>('violations');
    // Scan-issues (auth/permission/network failures) section is collapsed by default.
    const [showScanIssues, setShowScanIssues] = useState<boolean>(false);
    // Workspace scope for a manual scan. '__all__' scans every configured target
    // workspace in one aggregated run (matches the scheduled behavior); a specific
    // name scans just that one.
    const [workspaceSel, setWorkspaceSel] = useState<string>('__all__');
    const [targetWorkspaces, setTargetWorkspaces] = useState<TargetWorkspace[]>([]);
    const [environment, setEnvironment] = useState<'dev' | 'stage' | 'prod'>('prod');
    const [actionLoading, setActionLoading] = useState<string | null>(null);
    const [selectedViolation, setSelectedViolation] = useState<any | null>(null);
    const [executedActions, setExecutedActions] = useState<Record<string, { at: string }>>({});

    // In-report search + severity filter (on top of the policy-group tabs) and
    // incremental rendering. A completed run can carry tens of thousands of
    // violations; rendering them all at once hangs the tab, so we render a
    // growing window ("Load more") and let search/severity narrow the set first.
    const [violationSearch, setViolationSearch] = useState('');
    const [severityFilter, setSeverityFilter] = useState<string>('all');
    const [visibleCount, setVisibleCount] = useState<number>(VIOLATION_PAGE_SIZE);
    // Reset the render window whenever the visible set changes (run, tab, or filter).
    useEffect(() => {
        setVisibleCount(VIOLATION_PAGE_SIZE);
    }, [selectedRunId, activeTab, violationSearch, severityFilter]);

    // Server-side pagination and search states
    const [sentinelRuns, setSentinelRuns] = useState<any[]>([]);
    const [totalRuns, setTotalRuns] = useState(0);
    const [page, setPage] = useState(1);
    const [pageSize] = useState(10);
    const [searchQuery, setSearchQuery] = useState('');
    const [debouncedSearch, setDebouncedSearch] = useState('');
    const [isLoadingRuns, setIsLoadingRuns] = useState(false);

    const [schedules, setSchedules] = useState<any>(null);

    // On-demand digest modal
    const [digestOpen, setDigestOpen] = useState(false);
    const [digestInfo, setDigestInfo] = useState<any>(null);
    const [digestEmail, setDigestEmail] = useState('');
    const [digestSending, setDigestSending] = useState(false);
    const [digestResult, setDigestResult] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

    const openDigestModal = async () => {
        setDigestOpen(true);
        setDigestResult(null);
        try {
            const info = await api.getDigestInfo();
            setDigestInfo(info);
            setDigestEmail(info.default_recipient || '');
        } catch (e: any) {
            setDigestInfo(null);
            setDigestResult({ type: 'error', text: e?.message || 'Failed to load digest info' });
        }
    };

    const handleSendDigest = async () => {
        setDigestSending(true);
        setDigestResult(null);
        try {
            const res = await api.sendDigestNow(digestEmail.trim());
            setDigestResult({
                type: 'success',
                text: `Digest sent to ${res.recipient} (${res.violation_count} active violation${res.violation_count === 1 ? '' : 's'}).`,
            });
        } catch (e: any) {
            setDigestResult({ type: 'error', text: e?.message || 'Failed to send digest' });
        } finally {
            setDigestSending(false);
        }
    };

    // Debounce search query
    useEffect(() => {
        const timer = setTimeout(() => setDebouncedSearch(searchQuery), 300);
        return () => clearTimeout(timer);
    }, [searchQuery]);

    useEffect(() => {
        api.getSystemSchedules().then(setSchedules).catch(e => console.error("Failed to fetch schedules:", e));
        api.getTargetWorkspaces()
            .then(res => setTargetWorkspaces(res.workspaces || []))
            .catch(e => console.error("Failed to fetch target workspaces:", e));
    }, []);

    // Reset page on search change
    useEffect(() => {
        setPage(1);
    }, [debouncedSearch]);

    const fetchSentinelRuns = useCallback(async (isPolling = false) => {
        if (!isPolling) setIsLoadingRuns(true);
        try {
            const skip = (page - 1) * pageSize;
            const res = await api.getPaginatedRequests({
                skip,
                limit: pageSize,
                type: 'enforcement_sentinel',
                search: debouncedSearch || undefined,
                // List rows only need aggregate counts; the full violation records
                // are fetched on demand when a run is opened (see selectedRun below).
                summary: true,
            });
            setSentinelRuns(res.items);
            setTotalRuns(res.total);
        } catch(e) {
            console.error('Failed to fetch sentinel runs:', e);
        } finally {
            if (!isPolling) setIsLoadingRuns(false);
        }
    }, [page, pageSize, debouncedSearch]);

    useEffect(() => {
        fetchSentinelRuns();
    }, [fetchSentinelRuns]);

    // Full run records (with violations) fetched on demand when a row is opened.
    // The list itself is a lightweight summary, so we hydrate the selected run's
    // full payload here and cache it per id (immune to summary-list re-polling).
    const [fullRuns, setFullRuns] = useState<Record<string, any>>({});
    useEffect(() => {
        if (!selectedRunId || fullRuns[selectedRunId]) return;
        let cancelled = false;
        api.getRequest(selectedRunId)
            .then(full => { if (!cancelled) setFullRuns(prev => ({ ...prev, [selectedRunId]: full })); })
            .catch(e => console.error('Failed to load full sentinel run:', e));
        return () => { cancelled = true; };
    }, [selectedRunId, fullRuns]);

    // Prefer the hydrated full record; fall back to the light list row so the
    // panel opens instantly and upgrades once the full payload arrives.
    const selectedRun = useMemo(
        () => (selectedRunId ? fullRuns[selectedRunId] : undefined) || sentinelRuns.find(r => r.id === selectedRunId),
        [sentinelRuns, selectedRunId, fullRuns],
    );

    // Durably rehydrate the "Executed" state for a run from the server-side
    // audit records, so a page refresh doesn't lose the fact that an admin
    // already ran a manual enforcement action.
    useEffect(() => {
        if (!selectedRunId) return;
        let cancelled = false;
        api.getEnforcementActions(selectedRunId)
            .then(records => {
                if (cancelled) return;
                setExecutedActions(prev => {
                    const next = { ...prev };
                    for (const rec of records) {
                        const key = `${selectedRunId}-${rec.workspace || ''}-${rec.resource_id}-${rec.policy_name}-${rec.action}`;
                        if (!next[key]) {
                            next[key] = { at: rec.at ? formatPacific(rec.at, { year: undefined }) : '' };
                        }
                    }
                    return next;
                });
            })
            .catch(e => console.error('Failed to load enforcement actions:', e));
        return () => { cancelled = true; };
    }, [selectedRunId]);

    // Poll if any runs are actively running
    useEffect(() => {
        const hasActiveRuns = sentinelRuns.some(run => run.status !== 'completed' && run.status !== 'failed' && run.status !== 'rejected');
        
        if (hasActiveRuns) {
            const interval = setInterval(() => {
                fetchSentinelRuns(true);
            }, 3000);
            return () => clearInterval(interval);
        }
    }, [sentinelRuns, fetchSentinelRuns]);

    const getRunStatus = (run: any) => {
        const activeState = run.stateMachine?.states?.find((s: any) => s.isActive);
        if (run.status === 'completed') return <span className="flex items-center text-green-600 font-medium text-xs"><CheckCircle2 className="w-3 h-3 mr-1"/> Completed</span>;
        if (run.status === 'failed') return <span className="flex items-center text-red-600 font-medium text-xs"><AlertTriangle className="w-3 h-3 mr-1"/> Failed</span>;
        return <span className="flex items-center text-blue-600 font-medium text-xs"><Loader2 className="w-3 h-3 mr-1 animate-spin"/> {activeState?.name || 'Running'}</span>;
    };

    const handleRunSentinel = async () => {
        setIsRunning(true);
        try {
            // '__all__' => scan every target workspace (empty list); otherwise
            // scope to the single selected workspace by name.
            const workspaces = workspaceSel === '__all__' ? [] : [workspaceSel];
            await api.createRequest('enforcement_sentinel' as any, 'Manual Sentinel Run', environment, {
                workspaces,
                environment: environment,
            });
            
            // Instantly refresh the run list to show the newly added run
            if (page !== 1) {
                setPage(1); // This triggers useEffect
            } else {
                fetchSentinelRuns(); // Explicitly fetch if already on page 1 (don't await so UI unblocks faster)
            }
        } catch (e) {
            console.error(e);
            alert('Failed to start Sentinel run');
        } finally {
            setIsRunning(false);
        }
    };

    const handleExecuteAction = async (runId: string, v: any) => {
        if (!confirm(`Are you sure you want to manually execute the '${v.action}' action on ${resourceTypeLabel(v.resource_type)} ${v.resource_id}?`)) return;
        
        setActionLoading(v.resource_id);
        try {
            // Need to pass the token. Typically auth handles via cookies or we need to add the auth header if the app uses one.
            // The app uses API requests, which usually are authenticated implicitly if using cookies, or need a token.
            // Let's just do a normal fetch, relying on standard browser behavior or interceptors if any exist.
            const baseUrl = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1';
            const res = await fetch(`${baseUrl}/requests/${runId}/enforcement-action`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    resource_id: v.resource_id,
                    resource_type: v.resource_type,
                    action: v.action,
                    policy_name: v.policy,
                    reason: v.reason,
                    workspace_host: v?.workspace?.host || v?.input_context?.workspace?.host || undefined
                })
            });
            
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || 'Failed to execute action');
            
            setExecutedActions(prev => ({
                ...prev,
                [`${runId}-${resolveWorkspace(v)}-${v.resource_id}-${v.policy}-${v.action}`]: { at: formatPacific(new Date().toISOString(), { year: undefined }) }
            }));
        } catch (e: any) {
            console.error(e);
            alert(`Error executing action: ${e.message}`);
        } finally {
            setActionLoading(null);
        }
    };

    return (
        <div className="space-y-6">
            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <ShieldAlert className="w-5 h-5 text-gray-700" />
                        Enforcement Sentinel
                    </CardTitle>
                    <CardDescription>
                        The Automated Governance Pipeline discovers non-compliant resources and evaluates Open Policy Agent (OPA) policies across the environment.
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    {/* Primary action: a single "Run Scan". */}
                    <div className="bg-gray-50 border border-gray-200 rounded-md p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                        <div className="flex flex-col gap-1">
                            <div className="flex items-center gap-2 text-sm text-gray-800 font-medium">
                                <Search className="w-4 h-4" />
                                <span>Run a governance scan</span>
                            </div>
                            <p className="text-[11px] text-gray-500 max-w-2xl leading-relaxed">
                                Evaluates every resource against all policies and applies the safe, reversible
                                remediations automatically (certify / uncertify / warn the owner). Destructive
                                actions are never automated — they surface below for manual <span className="font-medium text-gray-700">Review &amp; Act</span>.
                            </p>
                            {schedules?.enforcement_sentinel?.next_run && (
                                <div className="text-xs text-gray-400 mt-0.5">
                                    Next scheduled run: {formatPacific(schedules.enforcement_sentinel.next_run, { year: undefined })}
                                </div>
                            )}
                        </div>

                        <div className="flex items-center gap-2 self-start sm:self-auto">
                            <Button
                                variant="outline"
                                onClick={openDigestModal}
                                className="h-9 whitespace-nowrap"
                            >
                                <Mail className="w-4 h-4 mr-1.5" />
                                Email Digest
                            </Button>
                            <Button
                                onClick={() => handleRunSentinel()}
                                disabled={isRunning}
                                className="h-9 whitespace-nowrap text-white"
                            >
                                <Search className="w-4 h-4 mr-1.5" />
                                {isRunning ? 'Starting...' : 'Run Scan'}
                            </Button>
                        </div>
                    </div>

                    {/* Everything else (scan scope + destructive enforcement) lives
                        behind an Advanced disclosure so the default surface stays simple. */}
                    <div className="mt-4">
                        <button
                            type="button"
                            onClick={() => setAdvancedOpen(o => !o)}
                            className="flex items-center gap-1.5 text-sm font-medium text-gray-600 hover:text-gray-900 transition-colors"
                            aria-expanded={advancedOpen}
                        >
                            <ChevronDown className={`w-4 h-4 transition-transform ${advancedOpen ? '' : '-rotate-90'}`} />
                            <SlidersHorizontal className="w-4 h-4" />
                            Advanced options
                        </button>

                        {advancedOpen && (
                            <div className="mt-3 border border-gray-200 rounded-md divide-y divide-gray-100 animate-in fade-in slide-in-from-top-1">
                                {/* Scan scope */}
                                <div className="p-4 flex flex-col gap-3">
                                    <div className="text-xs font-semibold uppercase tracking-wider text-gray-500">
                                        Scan scope
                                    </div>
                                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                                        <div className="flex flex-col gap-1">
                                            <label htmlFor="workspace" className="text-xs font-medium text-gray-700">
                                                Workspace
                                            </label>
                                            <select
                                                id="workspace"
                                                value={workspaceSel}
                                                onChange={(e) => {
                                                    const val = e.target.value;
                                                    setWorkspaceSel(val);
                                                    // Auto-derive environment from the selected workspace.
                                                    const ws = targetWorkspaces.find(w => w.name === val);
                                                    if (ws && ['dev', 'stage', 'prod'].includes(ws.environment)) {
                                                        setEnvironment(ws.environment as 'dev' | 'stage' | 'prod');
                                                    }
                                                }}
                                                className="flex h-8 w-full rounded-md border border-input bg-white px-2 py-1 text-xs ring-offset-background focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                                            >
                                                <option value="__all__">All target workspaces</option>
                                                {targetWorkspaces.map(w => (
                                                    <option key={w.name} value={w.name}>
                                                        {w.name}{w.environment ? ` (${w.environment})` : ''}
                                                    </option>
                                                ))}
                                            </select>
                                            <p className="text-[11px] text-gray-500 leading-relaxed">
                                                Scopes the <span className="font-medium text-gray-700">apps &amp; platform governance</span> policies
                                                (clusters, jobs, warehouses, dashboards, etc.). <span className="font-medium text-gray-700">All target workspaces</span> runs
                                                one aggregated scan across every configured workspace. Data certification is Unity Catalog&ndash;scoped and always
                                                runs once against the configured certification workspace, regardless of this selection.
                                            </p>
                                        </div>
                                        <div className="flex flex-col gap-1">
                                            <label htmlFor="environment" className="text-xs font-medium text-gray-700">
                                                Environment
                                            </label>
                                            <select
                                                id="environment"
                                                value={environment}
                                                onChange={(e) => setEnvironment(e.target.value as any)}
                                                className="flex h-8 w-full rounded-md border border-input bg-white px-2 py-1 text-xs ring-offset-background focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                                            >
                                                <option value="dev">dev</option>
                                                <option value="stage">stage</option>
                                                <option value="prod">prod</option>
                                            </select>
                                            <p className="text-[11px] text-gray-500 leading-relaxed">
                                                Environment context the platform-governance policies are evaluated under. Some rules are stricter in prod.
                                            </p>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        )}
                    </div>
                </CardContent>
            </Card>

            {/* Previous Runs Table */}
            <Card>
                <CardHeader className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-4">
                    <div>
                        <CardTitle className="text-lg">Run History</CardTitle>
                        <CardDescription>View previous enforcement audits, their findings, and executed actions.</CardDescription>
                    </div>
                    <div className="relative w-full md:w-64">
                        <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-gray-500" />
                        <input
                            type="text"
                            placeholder="Search runs..."
                            value={searchQuery}
                            onChange={(e) => setSearchQuery(e.target.value)}
                            className="flex h-9 w-full rounded-md border border-input bg-white pl-9 pr-3 py-1 text-sm shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                        />
                    </div>
                </CardHeader>
                <CardContent className="p-0">
                    <table className="w-full text-sm text-left">
                        <thead className="bg-gray-50 text-gray-900 font-medium border-b border-gray-200">
                            <tr>
                                <th className="p-3 pl-4">Run Date</th>
                                <th className="p-3">Status</th>
                                <th className="p-3">Found</th>
                                <th className="p-3">Issues</th>
                                <th className="p-3">Workspace</th>
                                <th className="p-3 text-right"></th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-100">
                            {sentinelRuns.length === 0 ? (
                                <tr>
                                    <td colSpan={6} className="p-6 text-center text-gray-500">
                                        No Sentinel runs found.
                                    </td>
                                </tr>
                            ) : (
                                sentinelRuns.map(run => {
                                    const ctx = (run as any).stateContext || run.metadata || (run as any).state_context || {};
                                    const violations = ctx.violations || [];

                                    // True failure count is the per-rule total in scan_stats. `violations.length`
                                    // under-counts (each entry aggregates all failed rules for one resource+policy)
                                    // and over-counts (it also includes CERTIFY/UNCERTIFY actions that aren't
                                    // failures). Prefer scan_stats; fall back to summing per-rule reasons, then to
                                    // the record count for older runs that predate scan_stats.
                                    const discoverFact = run.stateMachine?.states?.flatMap((s: any) => s.facts || []).find((f: any) => f.type === 'discover_completed');
                                    let vCount: number;
                                    if (ctx.scan_stats && typeof ctx.scan_stats.violation_count === 'number') {
                                        vCount = ctx.scan_stats.violation_count;
                                    } else if (discoverFact?.data?.violation_count !== undefined) {
                                        vCount = discoverFact.data.violation_count;
                                    } else {
                                        vCount = violations.reduce((sum: number, v: any) => (
                                            sum + (Array.isArray(v.violation_reasons) && v.violation_reasons.length > 0 ? v.violation_reasons.length : 1)
                                        ), 0);
                                    }

                                    return (
                                        <tr key={run.id} className="hover:bg-gray-50 transition-colors cursor-pointer group" onClick={() => setSelectedRunId(run.id)}>
                                            <td className="p-3 pl-4 font-medium text-gray-900">
                                                {formatPacific(run.createdAt)}
                                            </td>
                                            <td className="p-3">
                                                {getRunStatus(run)}
                                            </td>
                                            <td className="p-3 font-medium text-gray-600">
                                                {vCount > 0 ? (
                                                    <span className="text-red-600 font-bold">{vCount}</span>
                                                ) : (
                                                    <span className="text-green-600">0</span>
                                                )}
                                            </td>
                                            <td className="p-3">
                                                {(() => {
                                                    const failures: any[] = Array.isArray(ctx.workspace_failures) ? ctx.workspace_failures : [];
                                                    const hard = failures.filter((f: any) => !f.partial).length;
                                                    const partial = failures.filter((f: any) => f.partial).length;
                                                    if (hard === 0 && partial === 0) {
                                                        return <span className="text-gray-300">&mdash;</span>;
                                                    }
                                                    return (
                                                        <span className="inline-flex items-center gap-1">
                                                            <AlertTriangle className={`w-3.5 h-3.5 ${hard > 0 ? 'text-red-500' : 'text-amber-500'}`} />
                                                            <span className={`text-xs font-semibold ${hard > 0 ? 'text-red-600' : 'text-amber-600'}`}>
                                                                {hard > 0 ? `${hard} failed` : `${partial} partial`}
                                                            </span>
                                                        </span>
                                                    );
                                                })()}
                                            </td>
                                            <td className="p-3 text-gray-500">
                                                {(() => {
                                                    const names: string[] = Array.isArray(ctx.workspaces_scanned)
                                                        ? ctx.workspaces_scanned
                                                        : (ctx.workspace ? [ctx.workspace] : []);
                                                    if (names.length === 0) return <span className="text-gray-400">&mdash;</span>;
                                                    if (names.length === 1) return <span>{names[0]}</span>;
                                                    return (
                                                        <span title={names.join(', ')}>
                                                            {names.length} workspaces
                                                        </span>
                                                    );
                                                })()}
                                            </td>
                                            <td className="p-3 text-right">
                                                <Button variant="ghost" size="sm" className="h-8 text-gray-400 group-hover:text-gray-900 group-hover:bg-gray-200">
                                                    View Report <ArrowRight className="w-4 h-4 ml-2" />
                                                </Button>
                                            </td>
                                        </tr>
                                    );
                                })
                            )}
                        </tbody>
                    </table>
                    
                    {/* Pagination Controls */}
                    {totalRuns > 0 && (
                        <div className="flex items-center justify-between px-4 py-3 border-t border-gray-100 bg-gray-50">
                            <div className="text-xs text-gray-500">
                                Showing <span className="font-medium">{(page - 1) * pageSize + 1}</span> to <span className="font-medium">{Math.min(page * pageSize, totalRuns)}</span> of <span className="font-medium">{totalRuns}</span> runs
                            </div>
                            <div className="flex gap-1">
                                <Button 
                                    variant="outline" 
                                    size="sm" 
                                    onClick={() => setPage(p => Math.max(1, p - 1))}
                                    disabled={page === 1 || isLoadingRuns}
                                    className="h-7 text-xs px-2"
                                >
                                    <ChevronLeft className="w-3 h-3 mr-1" /> Prev
                                </Button>
                                <Button 
                                    variant="outline" 
                                    size="sm" 
                                    onClick={() => setPage(p => Math.min(Math.ceil(totalRuns / pageSize), p + 1))}
                                    disabled={page >= Math.ceil(totalRuns / pageSize) || isLoadingRuns}
                                    className="h-7 text-xs px-2"
                                >
                                    Next <ChevronRight className="w-3 h-3 ml-1" />
                                </Button>
                            </div>
                        </div>
                    )}
                </CardContent>
            </Card>

            {/* Modal for run details */}
            {selectedRun && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4 animate-in fade-in">
                    <div className="bg-white rounded-xl shadow-xl w-full max-w-[95vw] xl:max-w-[1600px] max-h-[95vh] flex flex-col overflow-hidden animate-in slide-in-from-bottom-4">
                        {/* Modal Header */}
                        <div className="flex items-center justify-between p-4 md:p-6 border-b border-gray-100">
                            <div>
                                <h2 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
                                    <ShieldAlert className="w-5 h-5 text-blue-600" />
                                    Sentinel Run Report
                                </h2>
                                <p className="text-sm text-gray-500 mt-1">
                                    {formatPacific(selectedRun.createdAt, { month: 'long', second: '2-digit' })} • 
                                    {((selectedRun as any).stateContext || selectedRun.metadata || (selectedRun as any).state_context || {}).workspace || 'ws-enterprise-prod'}
                                </p>
                            </div>
                            <Button variant="ghost" size="sm" onClick={() => { setSelectedRunId(null); setActiveTab('all'); setReportView('violations'); setShowScanIssues(false); }} className="rounded-full hover:bg-gray-100">
                                <X className="w-5 h-5 text-gray-500" />
                            </Button>
                        </div>

                        {(() => {
                            const ctx = (selectedRun as any).stateContext || selectedRun.metadata || (selectedRun as any).state_context || {};
                            const violations: any[] = ctx.violations || [];
                            const checks: any[] = ctx.checks || [];
                            const discoverFact = selectedRun.stateMachine?.states?.flatMap((s: any) => s.facts || []).find((f: any) => f.type === 'discover_completed');

                            // Flatten every (resource, policy) check into its individual policy
                            // rules so summary counts, tab counts, and the checklist are all
                            // per-rule (each check in a policy is represented) rather than one
                            // pass/fail per dataset. Policies without per-rule results fall back
                            // to a single synthetic rule row for that evaluation.
                            const ruleRows: any[] = checks.flatMap((c: any) => {
                                const rrs = Array.isArray(c.rule_results) ? c.rule_results : [];
                                if (rrs.length > 0) {
                                    return rrs.map((rr: any) => ({
                                        ...rr,
                                        result: rr.passed ? 'PASS' : 'VIOLATION',
                                        resource_id: c.resource_id,
                                        resource_type: c.resource_type,
                                        resource: c.resource,
                                        policy: c.policy,
                                        severity: rr.passed ? 'NONE' : c.severity,
                                    }));
                                }
                                return [{
                                    id: c.policy,
                                    description: (c.policy || '').replace(/_/g, ' '),
                                    passed: c.result === 'PASS',
                                    messages: c.violation_reasons || [],
                                    result: c.result,
                                    resource_id: c.resource_id,
                                    resource_type: c.resource_type,
                                    resource: c.resource,
                                    policy: c.policy,
                                    severity: c.severity,
                                }];
                            });

                            const assetsScanned = discoverFact?.data?.total_resources_scanned ?? '—';
                            const policiesEvaluated = discoverFact?.data?.policies_evaluated ?? '—';
                            const totalChecks = discoverFact?.data?.total_checks ?? ruleRows.length ?? '—';
                            const vCount = discoverFact?.data?.violation_count ?? ruleRows.filter((r: any) => !r.passed).length;
                            const passCount = discoverFact?.data?.pass_count ?? ruleRows.filter((r: any) => r.passed).length;

                            // Group violations by policy
                            const violationsByPolicy = violations.reduce((acc: any, v: any) => {
                                if (!acc[v.policy]) acc[v.policy] = [];
                                acc[v.policy].push(v);
                                return acc;
                            }, {});

                            const policyGroups = Object.keys(violationsByPolicy).sort();
                            
                            // Sort violations by severity (highest first)
                            const severityOrder: Record<string, number> = { 'CRITICAL': 4, 'HIGH': 3, 'MEDIUM': 2, 'LOW': 1, 'NONE': 0 };
                            const sortViolations = (vList: any[]) => [...vList].sort((a, b) => {
                                const sevA = severityOrder[a.severity] || 0;
                                const sevB = severityOrder[b.severity] || 0;
                                return sevB - sevA;
                            });

                            // Base set = the selected policy tab, then narrowed by the
                            // severity filter and free-text search (resource, policy,
                            // workspace, owner, action, severity, and reason text).
                            const baseViolations: any[] = activeTab === 'all' ? violations : (violationsByPolicy[activeTab] || []);
                            const q = violationSearch.trim().toLowerCase();
                            const matchesSearch = (v: any): boolean => {
                                if (!q) return true;
                                const reasons = Array.isArray(v.violation_reasons) ? v.violation_reasons.join(' ') : (v.reason || '');
                                const hay = [
                                    v.resource_id, resourceTypeLabel(v.resource_type), v.policy, v.action,
                                    v.severity, resolveWorkspace(v), resolveOwner(v), reasons,
                                ].filter(Boolean).join(' ').toLowerCase();
                                return hay.includes(q);
                            };
                            const filteredViolations = baseViolations.filter((v: any) =>
                                (severityFilter === 'all' || v.severity === severityFilter) && matchesSearch(v)
                            );
                            const filtersActive = q.length > 0 || severityFilter !== 'all';
                            const activeViolations = sortViolations(filteredViolations);
                            const visibleViolations = activeViolations.slice(0, visibleCount);

                            return (
                                <div className="flex-1 overflow-y-auto bg-gray-50/50 p-4 md:p-6 flex flex-col gap-6">
                                    {selectedRun.status === 'failed' ? (
                                        <div className="flex-1 flex flex-col items-center justify-center py-20 text-center">
                                            <AlertTriangle className="w-16 h-16 text-red-500 mb-6" />
                                            <h3 className="text-xl font-semibold text-gray-900 mb-2">
                                                Sentinel Run Failed
                                            </h3>
                                            <p className="text-red-600 font-mono text-sm bg-red-50 p-4 rounded-md border border-red-100 max-w-2xl text-left overflow-auto">
                                                {selectedRun.lastError?.error || 'An unexpected error occurred during the sentinel run. Check the backend logs for details.'}
                                            </p>
                                        </div>
                                    ) : selectedRun.status !== 'completed' && selectedRun.status !== 'rejected' ? (
                                        <div className="flex-1 flex flex-col items-center justify-center py-20 text-center">
                                            <Loader2 className="w-16 h-16 text-blue-500 animate-spin mb-6" />
                                            <h3 className="text-xl font-semibold text-gray-900 mb-2">
                                                {selectedRun.stateMachine?.states?.find((s: any) => s.isActive)?.name || 'Discovering Resources...'}
                                            </h3>
                                            <p className="text-gray-500 max-w-md">
                                                The Sentinel is actively scanning the workspace. This process can take a few minutes depending on the number of resources.
                                            </p>
                                        </div>
                                    ) : (
                                        <>
                                            {/* Scan issues: workspaces whose discovery failed (auth /
                                                permission / network). A failed workspace reports "0" but is
                                                NOT confirmed clean. Collapsed by default — a colored bar
                                                indicates issues exist; click to view the definitive cause. */}
                                            {(() => {
                                                const failures: any[] = Array.isArray(ctx.workspace_failures) ? ctx.workspace_failures : [];
                                                const scanError: string = typeof ctx.scan_error === 'string' ? ctx.scan_error : '';
                                                if (failures.length === 0 && !scanError) return null;
                                                const hard = failures.filter((f: any) => !f.partial);
                                                const partial = failures.filter((f: any) => f.partial);
                                                const isHard = hard.length > 0 || !!scanError;
                                                const CATEGORY_LABEL: Record<string, string> = {
                                                    authentication: 'Authentication / credentials',
                                                    authorization: 'Permissions',
                                                    network: 'Network / connectivity',
                                                    rate_limited: 'Rate limiting',
                                                    not_found: 'Missing API / endpoint',
                                                    unknown: 'Unclassified',
                                                };
                                                const headline = hard.length > 0
                                                    ? `${hard.length} workspace(s) returned no data — not confirmed clean`
                                                    : partial.length > 0
                                                        ? `${partial.length} workspace(s) returned partial results`
                                                        : 'Scan encountered errors';
                                                return (
                                                    <div className={`rounded-lg border overflow-hidden ${isHard ? 'bg-red-50 border-red-200' : 'bg-amber-50 border-amber-200'}`}>
                                                        <button
                                                            type="button"
                                                            onClick={() => setShowScanIssues(v => !v)}
                                                            className="w-full flex items-center gap-3 p-3 md:p-4 text-left hover:bg-black/5 transition-colors"
                                                        >
                                                            <AlertTriangle className={`w-5 h-5 flex-shrink-0 ${isHard ? 'text-red-600' : 'text-amber-600'}`} />
                                                            <div className="flex-1 min-w-0">
                                                                <div className={`font-semibold ${isHard ? 'text-red-900' : 'text-amber-900'}`}>
                                                                    Scan issues detected &mdash; {headline}
                                                                </div>
                                                                <div className={`text-xs mt-0.5 ${isHard ? 'text-red-700' : 'text-amber-700'}`}>
                                                                    {showScanIssues ? 'Click to hide details' : 'Click to view details (auth / permission / network)'}
                                                                </div>
                                                            </div>
                                                            <ChevronDown className={`w-4 h-4 flex-shrink-0 transition-transform ${isHard ? 'text-red-600' : 'text-amber-600'} ${showScanIssues ? '' : '-rotate-90'}`} />
                                                        </button>
                                                        {showScanIssues && (
                                                            <div className="px-3 md:px-4 pb-4 pt-1 border-t border-black/5">
                                                                <p className={`text-sm mb-3 ${isHard ? 'text-red-700' : 'text-amber-700'}`}>
                                                                    A scan that fails to authenticate or reach a workspace reports 0 findings, which does not mean the workspace is compliant. Resolve the cause below and re-run.
                                                                </p>
                                                                <div className="flex flex-col gap-2">
                                                                    {failures.map((f: any, i: number) => (
                                                                        <div key={i} className="text-sm bg-white/70 rounded-md border border-gray-200 p-2.5">
                                                                            <div className="flex flex-wrap items-center gap-2">
                                                                                <span className="font-semibold text-gray-900">{f.workspace || 'unknown'}</span>
                                                                                <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${f.partial ? 'bg-amber-100 text-amber-800' : 'bg-red-100 text-red-800'}`}>
                                                                                    {CATEGORY_LABEL[f.category] || f.category}
                                                                                </span>
                                                                                {f.partial && <span className="text-xs text-gray-500">partial ({f.failed}/{f.attempted})</span>}
                                                                            </div>
                                                                            <div className="text-xs text-gray-500 mt-1 space-y-0.5">
                                                                                {f.host && <div>Host: <span className="font-mono">{f.host}</span></div>}
                                                                                {f.credential_source && <div>Credentials: <span className="font-mono">{f.credential_source}</span></div>}
                                                                                {typeof f.network_reachable === 'boolean' && (
                                                                                    <div>
                                                                                        Network reachable:{' '}
                                                                                        <span className={f.network_reachable ? 'text-green-700 font-medium' : 'text-red-700 font-medium'}>
                                                                                            {f.network_reachable ? 'yes — credentials/permissions issue' : 'no — network/connectivity issue'}
                                                                                        </span>
                                                                                    </div>
                                                                                )}
                                                                                {(f.oauth_error || f.oauth_error_description) ? (
                                                                                    <div className="text-gray-700 break-words">
                                                                                        <span className="font-semibold">OAuth reason</span>{f.oauth_status ? ` (HTTP ${f.oauth_status})` : ''}:{' '}
                                                                                        <span className="font-mono">{f.oauth_error || 'error'}</span>{f.oauth_error_description ? ` — ${f.oauth_error_description}` : ''}
                                                                                    </div>
                                                                                ) : (
                                                                                    f.example && <div className="text-gray-600 break-words">Error: {f.example}</div>
                                                                                )}
                                                                            </div>
                                                                        </div>
                                                                    ))}
                                                                    {scanError && (
                                                                        <div className="text-xs text-gray-600 bg-white/70 rounded-md border border-gray-200 p-2.5 break-words">
                                                                            <span className="font-semibold text-gray-800">Other scan errors: </span>{scanError}
                                                                        </div>
                                                                    )}
                                                                </div>
                                                            </div>
                                                        )}
                                                    </div>
                                                );
                                            })()}

                                            {/* High level info cards */}
                                            <div className="flex flex-col gap-4">
                                                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                                                    <Card className="shadow-sm border-gray-200">
                                                        <CardContent className="p-4 flex flex-col gap-1">
                                                            <div className="flex items-center text-sm font-medium text-gray-500 mb-1">
                                                                <FileStack className="w-4 h-4 mr-2" /> Assets Scanned
                                                            </div>
                                                            <div className="text-2xl font-bold text-gray-900">{assetsScanned}</div>
                                                        </CardContent>
                                                    </Card>
                                                    <Card className="shadow-sm border-gray-200">
                                                        <CardContent className="p-4 flex flex-col gap-1">
                                                            <div className="flex items-center text-sm font-medium text-gray-500 mb-1">
                                                                <ShieldCheck className="w-4 h-4 mr-2" /> Policies Evaluated
                                                            </div>
                                                            <div className="text-2xl font-bold text-gray-900">{policiesEvaluated}</div>
                                                        </CardContent>
                                                    </Card>
                                                    <Card className="shadow-sm border-gray-200">
                                                        <CardContent className="p-4 flex flex-col gap-1">
                                                            <div className="flex items-center text-sm font-medium text-gray-500 mb-1">
                                                                <ListChecks className="w-4 h-4 mr-2" /> Total Checks
                                                            </div>
                                                            <div className="text-2xl font-bold text-gray-900">{totalChecks}</div>
                                                        </CardContent>
                                                    </Card>
                                                    {/* Combined pass/violation card. Falls back to violations-only
                                                        for older runs that didn't record a per-check pass count. */}
                                                    <Card className={`shadow-sm ${vCount > 0 ? 'bg-red-50/30 border-red-100' : 'bg-green-50/30 border-green-100'}`}>
                                                        <CardContent className="p-4 flex flex-col gap-2">
                                                            <div className="flex items-center text-sm font-medium text-gray-500">
                                                                <ListChecks className="w-4 h-4 mr-2" /> Result Summary
                                                            </div>
                                                            <div className="flex items-center gap-4">
                                                                {passCount !== null && passCount !== undefined && (
                                                                    <>
                                                                        <div className="flex flex-col">
                                                                            <div className="text-[10px] uppercase tracking-wider font-semibold text-green-700 flex items-center gap-1">
                                                                                <CheckCircle2 className="w-3 h-3" /> Pass
                                                                            </div>
                                                                            <div className="text-2xl font-bold text-green-600 leading-tight">{passCount}</div>
                                                                        </div>
                                                                        <div className="h-8 w-px bg-gray-200" />
                                                                    </>
                                                                )}
                                                                <div className="flex flex-col">
                                                                    <div className={`text-[10px] uppercase tracking-wider font-semibold flex items-center gap-1 ${vCount > 0 ? 'text-red-700' : 'text-gray-500'}`}>
                                                                        <AlertTriangle className="w-3 h-3" /> Violation
                                                                    </div>
                                                                    <div className={`text-2xl font-bold leading-tight ${vCount > 0 ? 'text-red-600' : 'text-green-600'}`}>{vCount}</div>
                                                                </div>
                                                            </div>
                                                        </CardContent>
                                                    </Card>
                                                </div>
                                                
                                                {/* Severity Breakdown. CRITICAL was collapsed out of the
                                                    policy rules, so severities are now HIGH / MEDIUM / LOW. */}
                                                {vCount > 0 && (
                                                    <div className="grid grid-cols-3 gap-4">
                                                        {['HIGH', 'MEDIUM', 'LOW'].map(sev => {
                                                            // Prefer the true breakdown from scan_stats (accurate even when
                                                            // the stored violation detail was truncated for very large runs);
                                                            // fall back to counting the loaded records for older runs.
                                                            const sevCounts = ctx.scan_stats?.severity_counts;
                                                            const count = (sevCounts && typeof sevCounts[sev] === 'number')
                                                                ? sevCounts[sev]
                                                                : violations.filter((v: any) => v.severity === sev).length;
                                                            const colors = sev === 'HIGH' && count > 0 ? 'bg-orange-50/30 border-orange-100' :
                                                                           sev === 'MEDIUM' && count > 0 ? 'bg-yellow-50/30 border-yellow-100' :
                                                                           sev === 'LOW' && count > 0 ? 'bg-gray-50/50 border-gray-200' :
                                                                           'bg-white border-gray-200 opacity-60';
                                                            const textColors = sev === 'HIGH' && count > 0 ? 'text-orange-600' :
                                                                               sev === 'MEDIUM' && count > 0 ? 'text-yellow-600' :
                                                                               sev === 'LOW' && count > 0 ? 'text-gray-700' :
                                                                               'text-gray-400';
                                                            return (
                                                                <Card key={sev} className={`shadow-sm ${colors}`}>
                                                                    <CardContent className="p-3 flex flex-col items-center justify-center gap-1">
                                                                        <div className="flex items-center text-xs font-semibold text-gray-500 uppercase tracking-wider">
                                                                            {sev}
                                                                        </div>
                                                                        <div className={`text-xl font-bold ${textColors}`}>{count}</div>
                                                                    </CardContent>
                                                                </Card>
                                                            );
                                                        })}
                                                    </div>
                                                )}
                                            </div>

                                            {/* Detailed Report Section */}
                                            <div className="bg-white border border-gray-200 rounded-lg shadow-sm flex flex-col overflow-hidden min-h-[400px]">
                                                {/* Combined toolbar: view toggle on the left, contextual
                                                    tabs/filters on the right. Single row to keep the
                                                    table visible without extra vertical chrome. */}
                                                <div className="flex items-center gap-3 border-b border-gray-100 bg-gray-50/80 px-2 py-2">
                                                    <div className="inline-flex rounded-md border border-gray-200 bg-white p-0.5 flex-shrink-0">
                                                        <button
                                                            onClick={() => setReportView('violations')}
                                                            className={`flex items-center gap-1.5 px-2.5 py-1 text-sm font-medium rounded transition-colors ${
                                                                reportView === 'violations'
                                                                    ? 'bg-gray-100 text-gray-900 shadow-sm ring-1 ring-gray-200'
                                                                    : 'text-gray-500 hover:text-gray-900'
                                                            }`}
                                                        >
                                                            <AlertTriangle className="w-4 h-4" /> Violations
                                                        </button>
                                                        <button
                                                            onClick={() => setReportView('checklist')}
                                                            className={`flex items-center gap-1.5 px-2.5 py-1 text-sm font-medium rounded transition-colors ${
                                                                reportView === 'checklist'
                                                                    ? 'bg-gray-100 text-gray-900 shadow-sm ring-1 ring-gray-200'
                                                                    : 'text-gray-500 hover:text-gray-900'
                                                            } ${checks.length === 0 ? 'opacity-50 cursor-not-allowed' : ''}`}
                                                            disabled={checks.length === 0}
                                                            title={checks.length === 0 ? 'No checklist data available for this run' : ''}
                                                        >
                                                            <ClipboardList className="w-4 h-4" /> Checklist
                                                        </button>
                                                    </div>
                                                    <div className="h-6 w-px bg-gray-200 flex-shrink-0" />
                                                    {reportView === 'violations' ? (
                                                        <div className="flex overflow-x-auto gap-2 hide-scrollbar flex-1 min-w-0">
                                                            <button
                                                                onClick={() => setActiveTab('all')}
                                                                className={`px-3 py-1 text-sm font-medium rounded-md whitespace-nowrap transition-colors flex-shrink-0 ${
                                                                    activeTab === 'all'
                                                                    ? 'bg-white text-gray-900 shadow-sm ring-1 ring-gray-200'
                                                                    : 'text-gray-500 hover:text-gray-900 hover:bg-gray-100'
                                                                }`}
                                                            >
                                                                All ({violations.length})
                                                            </button>
                                                            {policyGroups.map(policy => (
                                                                <button
                                                                    key={policy}
                                                                    onClick={() => setActiveTab(policy)}
                                                                    className={`px-3 py-1 text-sm font-medium rounded-md whitespace-nowrap transition-colors flex-shrink-0 ${
                                                                        activeTab === policy
                                                                        ? 'bg-white text-gray-900 shadow-sm ring-1 ring-gray-200'
                                                                        : 'text-gray-500 hover:text-gray-900 hover:bg-gray-100'
                                                                    }`}
                                                                >
                                                                    {policy.replace(/_/g, ' ')} ({violationsByPolicy[policy].length})
                                                                </button>
                                                            ))}
                                                        </div>
                                                    ) : (
                                                        <div className="flex-1 min-w-0 text-xs text-gray-400 truncate">
                                                            Every check evaluated this run — pass and violation.
                                                        </div>
                                                    )}
                                                </div>
                                                {reportView === 'violations' && (<>
                                                {/* Search + severity filter (narrows within the active policy tab). */}
                                                <div className="flex items-center gap-2 border-b border-gray-100 bg-white px-2 py-2">
                                                    <div className="relative flex-1 min-w-0">
                                                        <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 pointer-events-none" />
                                                        <input
                                                            type="text"
                                                            value={violationSearch}
                                                            onChange={(e) => setViolationSearch(e.target.value)}
                                                            placeholder="Search resource, owner, workspace, reason…"
                                                            className="w-full pl-8 pr-3 py-1.5 text-sm border border-gray-200 rounded-md focus:outline-none focus:ring-1 focus:ring-blue-400"
                                                        />
                                                    </div>
                                                    <select
                                                        value={severityFilter}
                                                        onChange={(e) => setSeverityFilter(e.target.value)}
                                                        className="text-sm border border-gray-200 rounded-md px-2 py-1.5 bg-white text-gray-700 focus:outline-none focus:ring-1 focus:ring-blue-400 flex-shrink-0"
                                                    >
                                                        <option value="all">All severities</option>
                                                        <option value="HIGH">High</option>
                                                        <option value="MEDIUM">Medium</option>
                                                        <option value="LOW">Low</option>
                                                    </select>
                                                    {filtersActive && (
                                                        <button
                                                            type="button"
                                                            onClick={() => { setViolationSearch(''); setSeverityFilter('all'); }}
                                                            className="text-xs text-gray-500 hover:text-gray-800 px-2 py-1 rounded-md hover:bg-gray-100 whitespace-nowrap flex-shrink-0"
                                                        >
                                                            Clear
                                                        </button>
                                                    )}
                                                    <span className="text-xs text-gray-400 whitespace-nowrap flex-shrink-0">
                                                        {filtersActive
                                                            ? `${activeViolations.length} of ${baseViolations.length}`
                                                            : `${baseViolations.length}`} shown
                                                    </span>
                                                </div>
                                                {/* Tab Content */}
                                                <div className="p-0 overflow-y-auto flex-1 max-h-[50vh]">
                                                    {activeViolations.length === 0 ? (
                                                        <div className="flex flex-col items-center justify-center h-full p-12 text-center">
                                                            {filtersActive ? (
                                                                <Search className="w-12 h-12 text-gray-300 mb-4" />
                                                            ) : (
                                                                <CheckCircle2 className="w-12 h-12 text-green-400 mb-4" />
                                                            )}
                                                            <h3 className="text-lg font-medium text-gray-900">
                                                                {filtersActive ? 'No matching violations' : 'No violations found'}
                                                            </h3>
                                                            <p className="text-gray-500 text-sm mt-1 max-w-sm">
                                                                {filtersActive
                                                                    ? 'No violations match your search / severity filter. Try clearing the filters.'
                                                                    : activeTab === 'all'
                                                                        ? 'All scanned resources are compliant with current policies.'
                                                                        : `No resources violated the ${activeTab.replace(/_/g, ' ')} policy group.`}
                                                            </p>
                                                        </div>
                                                    ) : (
                                                        <table className="w-full text-sm">
                                                            <thead className="bg-white sticky top-0 z-10 text-gray-500 font-medium border-b border-gray-200">
                                                                <tr>
                                                                    <th className="p-3 px-4 text-left">Resource</th>
                                                                    {activeTab === 'all' && <th className="p-3 text-left">Policy</th>}
                                                                    <th className="p-3 text-left">Workspace</th>
                                                                    <th className="p-3 text-left">Severity</th>
                                                                    <th className="p-3 text-left">Action</th>
                                                                    <th className="p-3 text-left w-1/3">Reason</th>
                                                                    <th className="p-3 text-right">Controls</th>
                                                                </tr>
                                                            </thead>
                                                            <tbody className="divide-y divide-gray-100">
                                                                {visibleViolations.map((v: any, idx: number) => (
                                                                    <tr key={idx} className="hover:bg-gray-50">
                                                                        <td className="p-3 px-4 font-mono text-xs text-gray-900">
                                                                            <div className="flex flex-col">
                                                                                <span className="text-[10px] text-gray-700 font-semibold uppercase tracking-wider font-sans mb-0.5">{resourceTypeLabel(v.resource_type)}</span>
                                                                                <span className="break-all">{v.resource_id}</span>
                                                                                <span className="mt-1 flex items-center gap-1 font-sans text-[10px] text-gray-500">
                                                                                    <span className="uppercase tracking-wider">Owner</span>
                                                                                    <OwnerCell owner={resolveOwner(v)} />
                                                                                </span>
                                                                            </div>
                                                                        </td>
                                                                        {activeTab === 'all' && <td className="p-3 text-gray-700">{v.policy.replace(/_/g, ' ')}</td>}
                                                                        <td className="p-3 text-gray-600">
                                                                            {resolveWorkspace(v)
                                                                                ? <span className="text-xs px-1.5 py-0.5 bg-gray-100 rounded-md whitespace-nowrap">{resolveWorkspace(v)}</span>
                                                                                : <span className="text-gray-400">&mdash;</span>}
                                                                        </td>
                                                                        <td className="p-3">
                                                                            <span className={`text-[10px] uppercase font-bold px-2 py-1 rounded-full ${
                                                                                v.severity === 'CRITICAL' ? 'bg-red-100 text-red-800 border border-red-200' :
                                                                                v.severity === 'HIGH' ? 'bg-orange-100 text-orange-800 border border-orange-200' :
                                                                                v.severity === 'MEDIUM' ? 'bg-yellow-100 text-yellow-800 border border-yellow-200' :
                                                                                'bg-gray-100 text-gray-800 border border-gray-200'
                                                                            }`}>
                                                                                {v.severity}
                                                                            </span>
                                                                        </td>
                                                                        <td className="p-3 font-mono text-xs font-bold text-gray-700">{v.action}</td>
                                                                        <td className="p-3 text-xs text-gray-600 break-words leading-relaxed">{formatReason(v)}</td>
                                                                            <td className="p-3 text-right">
                                                                                {(() => {
                                                                                    const execKey = `${selectedRun.id}-${resolveWorkspace(v)}-${v.resource_id}-${v.policy}-${v.action}`;
                                                                                    const executed = executedActions[execKey];
                                                                                    if (executed) {
                                                                                        return (
                                                                                            <div className="flex flex-col items-end">
                                                                                                <span className="text-xs font-semibold text-green-600 flex items-center">
                                                                                                    <CheckCircle2 className="w-3 h-3 mr-1" /> Executed
                                                                                                </span>
                                                                                                <span className="text-[10px] text-gray-500">by you on {executed.at}</span>
                                                                                            </div>
                                                                                        );
                                                                                    }
                                                                                    // Keep the button visible even when the resolved
                                                                                    // action is a no-op (e.g. WARN / KEEP_*), but disable
                                                                                    // it with an explanatory tooltip so it never looks
                                                                                    // like the control is missing.
                                                                                    const actionable = ['KILL', 'CERTIFY', 'UNCERTIFY'].includes(v.action);
                                                                                    return actionable ? (
                                                                                        <Button 
                                                                                            size="sm" 
                                                                                            variant="outline"
                                                                                            className="text-xs h-7 px-2 border-blue-200 text-blue-600 hover:bg-blue-50 hover:text-blue-700"
                                                                                            onClick={() => setSelectedViolation(v)}
                                                                                        >
                                                                                            Review and Act
                                                                                        </Button>
                                                                                    ) : (
                                                                                        <Button 
                                                                                            size="sm" 
                                                                                            variant="outline"
                                                                                            disabled
                                                                                            className="text-xs h-7 px-2 text-gray-400 border-gray-200 cursor-not-allowed"
                                                                                            title="No action required — this check does not trigger an automated enforcement step."
                                                                                        >
                                                                                            Review and Act
                                                                                        </Button>
                                                                                    );
                                                                })()}
                                                                            </td>
                                                                    </tr>
                                                                ))}
                                                            </tbody>
                                                        </table>
                                                    )}
                                                    {activeViolations.length > visibleViolations.length && (
                                                        <div className="sticky bottom-0 flex items-center justify-center gap-3 p-2.5 border-t border-gray-100 bg-gray-50/90 backdrop-blur-sm">
                                                            <span className="text-xs text-gray-500">
                                                                Showing {visibleViolations.length} of {activeViolations.length}
                                                            </span>
                                                            <Button
                                                                size="sm"
                                                                variant="outline"
                                                                className="text-xs h-7 px-3"
                                                                onClick={() => setVisibleCount(c => c + VIOLATION_PAGE_SIZE)}
                                                            >
                                                                Load more
                                                            </Button>
                                                            <Button
                                                                size="sm"
                                                                variant="ghost"
                                                                className="text-xs h-7 px-3 text-gray-500"
                                                                onClick={() => setVisibleCount(activeViolations.length)}
                                                            >
                                                                Show all ({activeViolations.length})
                                                            </Button>
                                                        </div>
                                                    )}
                                                </div>
                                                </>)}
                                                {reportView === 'checklist' && (
                                                    <CertificationChecklist ruleRows={ruleRows} />
                                                )}
                                            </div>
                                        </>
                                    )}
                                </div>
                            );
                        })()}
                    </div>
                </div>
            )}
            {/* Review and Act Modal */}
            {selectedViolation && selectedRun && (
                <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4 animate-in fade-in">
                    <div className="bg-white rounded-xl shadow-xl w-full max-w-3xl max-h-[90vh] flex flex-col overflow-hidden animate-in zoom-in-95">
                        <div className="flex items-center justify-between p-4 border-b border-gray-100 bg-white">
                            <h3 className="text-lg font-semibold text-gray-900">Review and Act: {selectedViolation.resource_id}</h3>
                            <Button variant="ghost" size="sm" onClick={() => setSelectedViolation(null)} className="rounded-full hover:bg-gray-100">
                                <X className="w-5 h-5 text-gray-500" />
                            </Button>
                        </div>
                        <div className="p-6 overflow-y-auto flex-1 space-y-6">
                            <div className="grid grid-cols-2 gap-4 text-sm bg-gray-50 p-4 rounded-lg border border-gray-100">
                                <div><span className="font-semibold text-gray-500 block mb-1">Resource Type</span> {resourceTypeLabel(selectedViolation.resource_type)}</div>
                                <div><span className="font-semibold text-gray-500 block mb-1">Policy</span> {selectedViolation.policy}</div>
                                <div><span className="font-semibold text-gray-500 block mb-1">Workspace</span> {resolveWorkspace(selectedViolation) || <span className="text-gray-400">Unknown</span>}</div>
                                <div>
                                    <span className="font-semibold text-gray-500 block mb-1">Severity</span>
                                    <span className={`text-[10px] uppercase font-bold px-2 py-1 rounded-full ${
                                        selectedViolation.severity === 'CRITICAL' ? 'bg-red-100 text-red-800 border border-red-200' :
                                        selectedViolation.severity === 'HIGH' ? 'bg-orange-100 text-orange-800 border border-orange-200' :
                                        selectedViolation.severity === 'MEDIUM' ? 'bg-yellow-100 text-yellow-800 border border-yellow-200' :
                                        'bg-gray-100 text-gray-800 border border-gray-200'
                                    }`}>
                                        {selectedViolation.severity}
                                    </span>
                                </div>
                                <div><span className="font-semibold text-gray-500 block mb-1">Action</span> <span className="font-mono font-bold text-gray-700">{selectedViolation.action}</span></div>
                                <div className="col-span-2"><span className="font-semibold text-gray-500 block mb-1">Owner</span> <OwnerCell owner={resolveOwner(selectedViolation)} /></div>
                                <div className="col-span-2"><span className="font-semibold text-gray-500 block mb-1">Reason</span> <div className="mt-2 text-gray-700 leading-relaxed">{formatReason(selectedViolation)}</div></div>
                            </div>
                            
                            <div>
                                <h4 className="text-sm font-semibold text-gray-900 mb-2">Full Context Data</h4>
                                <pre className="bg-gray-900 p-4 rounded-lg border border-gray-700 text-xs font-mono text-green-400 overflow-x-auto whitespace-pre-wrap break-words shadow-inner">
                                    {JSON.stringify(selectedViolation, null, 2)}
                                </pre>
                            </div>
                        </div>
                        <div className="p-4 border-t border-gray-100 bg-gray-50 flex justify-end gap-3 shrink-0">
                            <Button variant="outline" onClick={() => setSelectedViolation(null)}>Cancel</Button>
                            <Button 
                                variant="default"
                                disabled={actionLoading === selectedViolation.resource_id}
                                onClick={async () => {
                                    await handleExecuteAction(selectedRun.id, selectedViolation);
                                    setSelectedViolation(null);
                                }}
                                className="flex items-center bg-red-600 hover:bg-red-700 text-white"
                            >
                                {actionLoading === selectedViolation.resource_id ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : null}
                                Execute Action
                            </Button>
                        </div>
                    </div>
                </div>
            )}

            {/* On-demand digest modal */}
            {digestOpen && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" onClick={() => !digestSending && setDigestOpen(false)}>
                    <div className="bg-white rounded-xl shadow-xl w-full max-w-lg overflow-hidden" onClick={(e) => e.stopPropagation()}>
                        <div className="p-5 border-b border-gray-100 flex items-start justify-between gap-4">
                            <div className="flex items-center gap-2">
                                <Mail className="w-5 h-5 text-gray-700" />
                                <h3 className="text-lg font-semibold text-gray-900">Email governance digest</h3>
                            </div>
                            <button onClick={() => !digestSending && setDigestOpen(false)} className="text-gray-400 hover:text-gray-600">
                                <X className="w-5 h-5" />
                            </button>
                        </div>

                        <div className="p-5 space-y-4">
                            <div className="flex items-start gap-2 bg-blue-50 border border-blue-200 text-blue-800 p-3 rounded-md text-sm">
                                <Clock className="w-4 h-4 mt-0.5 flex-shrink-0" />
                                <div>
                                    {digestInfo ? (
                                        <>
                                            <span>The digest is scheduled <strong>{digestInfo.label?.toLowerCase()}</strong>
                                            {digestInfo.next_run ? <> — next send {formatPacific(digestInfo.next_run, { year: undefined })}</> : null}.</span>
                                            <span> You can send it now to any address below.</span>
                                        </>
                                    ) : (
                                        <span>Send the current digest now to any address below.</span>
                                    )}
                                </div>
                            </div>

                            {digestInfo && (
                                <div className="text-sm text-gray-600">
                                    {digestInfo.latest_run_at ? (
                                        <>Based on the latest scan from <span className="font-medium text-gray-800">{formatPacific(digestInfo.latest_run_at, { year: undefined })}</span> — <span className="font-medium text-gray-800">{digestInfo.active_violations}</span> active violation{digestInfo.active_violations === 1 ? '' : 's'}.</>
                                    ) : (
                                        <span className="text-amber-700">No completed scan yet — run a scan first to generate a digest.</span>
                                    )}
                                </div>
                            )}

                            <div className="space-y-1.5">
                                <label className="text-sm font-medium text-gray-800">Recipient email</label>
                                <input
                                    type="text"
                                    value={digestEmail}
                                    onChange={(e) => setDigestEmail(e.target.value)}
                                    placeholder="name@company.com"
                                    className="w-full px-3 py-2 border rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 border-gray-200"
                                />
                                <p className="text-xs text-gray-500">Separate multiple addresses with commas.</p>
                            </div>

                            {digestResult && (
                                <div className={`p-3 rounded-md text-sm ${digestResult.type === 'success' ? 'bg-green-50 border border-green-200 text-green-800' : 'bg-red-50 border border-red-200 text-red-800'}`}>
                                    {digestResult.text}
                                </div>
                            )}
                        </div>

                        <div className="p-4 border-t border-gray-100 bg-gray-50 flex justify-end gap-3">
                            <Button variant="outline" onClick={() => setDigestOpen(false)} disabled={digestSending}>Close</Button>
                            <Button
                                onClick={handleSendDigest}
                                disabled={digestSending || !digestEmail.trim() || (digestInfo && !digestInfo.latest_run_id)}
                                className="flex items-center text-white"
                            >
                                {digestSending ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Send className="w-4 h-4 mr-2" />}
                                Send now
                            </Button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
