import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { ShieldAlert, AlertTriangle, Search, Unlock, Lock, CheckCircle2, Loader2, X, FileStack, ShieldCheck, ListChecks, ArrowRight, ChevronLeft, ChevronRight, ClipboardList, XCircle } from 'lucide-react';
import { api } from '../../services/api';
import { useState, useMemo, useEffect, useCallback } from 'react';
import { format, parseISO } from 'date-fns';

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

export function EnforcementSentinel() {
    const [isRunning, setIsRunning] = useState(false);
    const [isEnforcementUnlocked, setIsEnforcementUnlocked] = useState(false);
    const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
    const [activeTab, setActiveTab] = useState<string>('all');
    // Top-level toggle: "Violations" shows only failed checks (legacy view).
    // "Checklist" shows every (resource, policy) evaluation — PASS and
    // VIOLATION — so reviewers can audit what was actually verified.
    const [reportView, setReportView] = useState<'violations' | 'checklist'>('violations');
    const [checklistFilter, setChecklistFilter] = useState<'all' | 'pass' | 'violation'>('all');
    const [workspace, setWorkspace] = useState('ws-enterprise-prod');
    const [environment, setEnvironment] = useState<'dev' | 'stage' | 'prod'>('prod');
    const [actionLoading, setActionLoading] = useState<string | null>(null);
    const [selectedViolation, setSelectedViolation] = useState<any | null>(null);
    const [executedActions, setExecutedActions] = useState<Record<string, { at: string }>>({});

    // Server-side pagination and search states
    const [sentinelRuns, setSentinelRuns] = useState<any[]>([]);
    const [totalRuns, setTotalRuns] = useState(0);
    const [page, setPage] = useState(1);
    const [pageSize] = useState(10);
    const [searchQuery, setSearchQuery] = useState('');
    const [debouncedSearch, setDebouncedSearch] = useState('');
    const [isLoadingRuns, setIsLoadingRuns] = useState(false);

    const [schedules, setSchedules] = useState<any>(null);

    // Debounce search query
    useEffect(() => {
        const timer = setTimeout(() => setDebouncedSearch(searchQuery), 300);
        return () => clearTimeout(timer);
    }, [searchQuery]);

    useEffect(() => {
        api.getSystemSchedules().then(setSchedules).catch(e => console.error("Failed to fetch schedules:", e));
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
                search: debouncedSearch || undefined
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

    const selectedRun = useMemo(() => sentinelRuns.find(r => r.id === selectedRunId), [sentinelRuns, selectedRunId]);

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

    const handleRunSentinel = async (mode: 'audit_only' | 'active_enforcement') => {
        const modeLabel = mode === 'audit_only' ? 'Audit Only' : 'Active Enforcement';
        
        if (mode === 'active_enforcement') {
            if (!confirm(`Run the Enforcement Sentinel across the environment in ${modeLabel} mode?`)) return;
        }
        
        setIsRunning(true);
        try {
            await api.createRequest('enforcement_sentinel' as any, `Manual Sentinel Run (${modeLabel})`, environment, {
                enforcement_mode: mode,
                workspace: workspace,
                environment: environment
            });
            
            // Instantly refresh the run list to show the newly added run
            if (page !== 1) {
                setPage(1); // This triggers useEffect
            } else {
                fetchSentinelRuns(); // Explicitly fetch if already on page 1 (don't await so UI unblocks faster)
            }
            
            if (mode === 'active_enforcement') {
                alert('Sentinel run started! Check the requests list on the Dashboard to track progress.');
            }
        } catch (e) {
            console.error(e);
            alert('Failed to start Sentinel run');
        } finally {
            setIsRunning(false);
            if (mode === 'active_enforcement') {
                setIsEnforcementUnlocked(false);
            }
        }
    };

    const handleExecuteAction = async (runId: string, v: any) => {
        if (!confirm(`Are you sure you want to manually execute the '${v.action}' action on ${v.resource_type} ${v.resource_id}?`)) return;
        
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
                    reason: v.reason
                })
            });
            
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || 'Failed to execute action');
            
            setExecutedActions(prev => ({
                ...prev,
                [`${runId}-${v.resource_id}-${v.policy}-${v.action}`]: { at: format(new Date(), 'MMM d, HH:mm') }
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
                    <div className="bg-gray-50 border border-gray-200 rounded-md p-3 mb-6 flex flex-wrap items-center justify-between gap-4">
                        <div className="flex flex-col gap-1">
                            <div className="flex items-center gap-2 text-sm text-gray-700 font-medium">
                                <Search className="w-4 h-4" />
                                <span>Manual Trigger</span>
                            </div>
                            {schedules?.enforcement_sentinel?.next_run && (
                                <div className="text-xs text-gray-500">
                                    Next scheduled run: {format(parseISO(schedules.enforcement_sentinel.next_run), 'MMM d, HH:mm')}
                                </div>
                            )}
                        </div>

                        <div className="flex items-center gap-3 flex-1 max-w-2xl justify-end">
                            <div className="flex items-center gap-4 flex-1">
                                <div className="flex items-center gap-2 flex-1 max-w-xs">
                                    <label htmlFor="workspace" className="text-xs font-medium text-gray-500 whitespace-nowrap">
                                        Workspace:
                                    </label>
                                    <input
                                        type="text"
                                        id="workspace"
                                        value={workspace}
                                        onChange={(e) => setWorkspace(e.target.value)}
                                        className="flex h-8 w-full rounded-md border border-input bg-white px-2 py-1 text-xs ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
                                        placeholder="ws-enterprise-prod"
                                    />
                                </div>
                                <div className="flex items-center gap-2 flex-1 max-w-[150px]">
                                    <label htmlFor="environment" className="text-xs font-medium text-gray-500 whitespace-nowrap">
                                        Env:
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
                                </div>
                            </div>

                            <Button 
                                onClick={() => handleRunSentinel('audit_only')} 
                                variant="outline" 
                                disabled={isRunning}
                                size="sm"
                                className="h-8 bg-white"
                            >
                                <Search className="w-3 h-3 mr-1" />
                                {isRunning ? 'Starting...' : 'Run Audit'}
                            </Button>

                            <div className={`flex items-center transition-colors duration-300 rounded-md border ${isEnforcementUnlocked ? 'bg-red-50 border-red-200 shadow-sm' : 'bg-white border-gray-200'}`}>
                                {!isEnforcementUnlocked ? (
                                        <Button 
                                            onClick={() => setIsEnforcementUnlocked(true)} 
                                            variant="outline"
                                            disabled={isRunning}
                                        size="sm"
                                        className="h-8 border-0 bg-transparent hover:bg-gray-100 text-xs"
                                    >
                                        <Unlock className="w-3 h-3 mr-1 text-gray-500" />
                                        Unlock Enforcement
                                    </Button>
                                ) : (
                                    <div className="flex items-center animate-in fade-in slide-in-from-left-2">
                                        <Button 
                                            onClick={() => handleRunSentinel('active_enforcement')} 
                                            variant="default"
                                            disabled={isRunning}
                                            size="sm"
                                            className="h-8 rounded-r-none text-xs"
                                        >
                                            <AlertTriangle className="w-3 h-3 mr-1" />
                                            {isRunning ? 'Starting...' : 'Execute'}
                                        </Button>
                                        <Button 
                                            onClick={() => setIsEnforcementUnlocked(false)} 
                                            variant="ghost"
                                            disabled={isRunning}
                                            size="sm"
                                            className="text-gray-500 hover:text-gray-700 h-8 rounded-l-none text-xs px-2"
                                        >
                                            <Lock className="w-3 h-3" />
                                        </Button>
                                    </div>
                                )}
                            </div>
                        </div>
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
                                <th className="p-3">Mode</th>
                                <th className="p-3">Status</th>
                                <th className="p-3">Found</th>
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
                                    const mode = ctx.enforcement_mode === 'active_enforcement' ? 'Enforcement' : 'Audit Only';
                                    const violations = ctx.violations || [];
                                    
                                    const discoverFact = run.stateMachine?.states?.flatMap((s: any) => s.facts || []).find((f: any) => f.type === 'discover_completed');
                                    let vCount = violations.length;
                                    if (discoverFact && discoverFact.data && discoverFact.data.violation_count !== undefined) {
                                        vCount = discoverFact.data.violation_count;
                                    }

                                    return (
                                        <tr key={run.id} className="hover:bg-gray-50 transition-colors cursor-pointer group" onClick={() => setSelectedRunId(run.id)}>
                                            <td className="p-3 pl-4 font-medium text-gray-900">
                                                {format(parseISO(run.createdAt), 'MMM d, yyyy HH:mm')}
                                            </td>
                                            <td className="p-3">
                                                <span className={`px-2 py-1 rounded text-xs ${mode === 'Enforcement' ? 'bg-red-100 text-red-800' : 'bg-gray-100 text-gray-800'}`}>
                                                    {mode}
                                                </span>
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
                                            <td className="p-3 text-gray-500">
                                                {ctx.workspace || 'ws-enterprise-prod'} <span className="text-xs ml-1 px-1.5 py-0.5 bg-gray-100 rounded-md">{ctx.environment || 'prod'}</span>
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
                                    {format(parseISO(selectedRun.createdAt), 'MMMM d, yyyy @ HH:mm:ss')} • 
                                    {((selectedRun as any).stateContext || selectedRun.metadata || (selectedRun as any).state_context || {}).workspace || 'ws-enterprise-prod'}
                                </p>
                            </div>
                            <Button variant="ghost" size="sm" onClick={() => { setSelectedRunId(null); setActiveTab('all'); setReportView('violations'); setChecklistFilter('all'); }} className="rounded-full hover:bg-gray-100">
                                <X className="w-5 h-5 text-gray-500" />
                            </Button>
                        </div>

                        {(() => {
                            const ctx = (selectedRun as any).stateContext || selectedRun.metadata || (selectedRun as any).state_context || {};
                            const violations: any[] = ctx.violations || [];
                            const checks: any[] = ctx.checks || [];
                            const discoverFact = selectedRun.stateMachine?.states?.flatMap((s: any) => s.facts || []).find((f: any) => f.type === 'discover_completed');
                            
                            const assetsScanned = discoverFact?.data?.total_resources_scanned ?? '—';
                            const policiesEvaluated = discoverFact?.data?.policies_evaluated ?? '—';
                            const totalChecks = discoverFact?.data?.total_checks ?? checks.length ?? '—';
                            const vCount = discoverFact?.data?.violation_count ?? violations.length;
                            const passCount = discoverFact?.data?.pass_count ?? (checks.length ? checks.length - violations.length : null);

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

                            const activeViolations = sortViolations(activeTab === 'all' ? violations : (violationsByPolicy[activeTab] || []));

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
                                                
                                                {/* Severity Breakdown */}
                                                {vCount > 0 && (
                                                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                                                        {['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'].map(sev => {
                                                            const count = violations.filter((v: any) => v.severity === sev).length;
                                                            const colors = sev === 'CRITICAL' && count > 0 ? 'bg-red-50/30 border-red-100' :
                                                                           sev === 'HIGH' && count > 0 ? 'bg-orange-50/30 border-orange-100' :
                                                                           sev === 'MEDIUM' && count > 0 ? 'bg-yellow-50/30 border-yellow-100' :
                                                                           'bg-white border-gray-200 opacity-60';
                                                            const textColors = sev === 'CRITICAL' && count > 0 ? 'text-red-600' :
                                                                               sev === 'HIGH' && count > 0 ? 'text-orange-600' :
                                                                               sev === 'MEDIUM' && count > 0 ? 'text-yellow-600' :
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
                                                        <div className="flex gap-2 flex-1 min-w-0">
                                                            {[
                                                                { id: 'all', label: `All (${checks.length})` },
                                                                { id: 'pass', label: `Passed (${checks.filter((c: any) => c.result === 'PASS').length})` },
                                                                { id: 'violation', label: `Violations (${checks.filter((c: any) => c.result === 'VIOLATION').length})` },
                                                            ].map(opt => (
                                                                <button
                                                                    key={opt.id}
                                                                    onClick={() => setChecklistFilter(opt.id as any)}
                                                                    className={`px-3 py-1 text-sm font-medium rounded-md whitespace-nowrap transition-colors flex-shrink-0 ${
                                                                        checklistFilter === opt.id
                                                                            ? 'bg-white text-gray-900 shadow-sm ring-1 ring-gray-200'
                                                                            : 'text-gray-500 hover:text-gray-900 hover:bg-gray-100'
                                                                    }`}
                                                                >
                                                                    {opt.label}
                                                                </button>
                                                            ))}
                                                        </div>
                                                    )}
                                                </div>
                                                {reportView === 'violations' && (<>
                                                {/* Tab Content */}
                                                <div className="p-0 overflow-y-auto flex-1 max-h-[50vh]">
                                                    {activeViolations.length === 0 ? (
                                                        <div className="flex flex-col items-center justify-center h-full p-12 text-center">
                                                            <CheckCircle2 className="w-12 h-12 text-green-400 mb-4" />
                                                            <h3 className="text-lg font-medium text-gray-900">No violations found</h3>
                                                            <p className="text-gray-500 text-sm mt-1 max-w-sm">
                                                                {activeTab === 'all' 
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
                                                                    <th className="p-3 text-left">Severity</th>
                                                                    <th className="p-3 text-left">Action</th>
                                                                    <th className="p-3 text-left w-1/3">Reason</th>
                                                                    {ctx.enforcement_mode === 'audit_only' && (
                                                                        <th className="p-3 text-right">Controls</th>
                                                                    )}
                                                                </tr>
                                                            </thead>
                                                            <tbody className="divide-y divide-gray-100">
                                                                {activeViolations.map((v: any, idx: number) => (
                                                                    <tr key={idx} className="hover:bg-gray-50">
                                                                        <td className="p-3 px-4 font-mono text-xs text-gray-900">
                                                                            <div className="flex flex-col">
                                                                                <span className="text-[10px] text-gray-700 font-semibold uppercase tracking-wider font-sans mb-0.5">{v.resource_type}</span>
                                                                                {v.resource_id}
                                                                            </div>
                                                                        </td>
                                                                        {activeTab === 'all' && <td className="p-3 text-gray-700">{v.policy.replace(/_/g, ' ')}</td>}
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
                                                                        {ctx.enforcement_mode === 'audit_only' && (
                                                                            <td className="p-3 text-right">
                                                                                {(() => {
                                                                                    const execKey = `${selectedRun.id}-${v.resource_id}-${v.policy}-${v.action}`;
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
                                                                                    return ['KILL', 'CERTIFY', 'UNCERTIFY'].includes(v.action) && (
                                                                                        <Button 
                                                                                            size="sm" 
                                                                                            variant="outline"
                                                                                            className="text-xs h-7 px-2 border-blue-200 text-blue-600 hover:bg-blue-50 hover:text-blue-700"
                                                                                            onClick={() => setSelectedViolation(v)}
                                                                                        >
                                                                                            Review and Act
                                                                                        </Button>
                                                                                    );
                                                                                })()}
                                                                            </td>
                                                                        )}
                                                                    </tr>
                                                                ))}
                                                            </tbody>
                                                        </table>
                                                    )}
                                                </div>
                                                </>)}
                                                {reportView === 'checklist' && (
                                                    <div className="p-0 overflow-y-auto flex-1 max-h-[60vh]">
                                                        {(() => {
                                                            if (checks.length === 0) {
                                                                return (
                                                                    <div className="flex flex-col items-center justify-center h-full p-12 text-center">
                                                                        <ClipboardList className="w-12 h-12 text-gray-400 mb-4" />
                                                                        <h3 className="text-lg font-medium text-gray-900">No checklist data available</h3>
                                                                        <p className="text-gray-500 text-sm mt-1 max-w-sm">
                                                                            This run did not record per-check evaluations. Re-run the Sentinel to capture a full audit checklist.
                                                                        </p>
                                                                    </div>
                                                                );
                                                            }

                                                            const filtered = checks.filter((c: any) => {
                                                                if (checklistFilter === 'pass') return c.result === 'PASS';
                                                                if (checklistFilter === 'violation') return c.result === 'VIOLATION';
                                                                return true;
                                                            });

                                                            // Violations first (by severity desc), then passes (by resource).
                                                            const severityRank: Record<string, number> = { 'CRITICAL': 4, 'HIGH': 3, 'MEDIUM': 2, 'LOW': 1, 'NONE': 0 };
                                                            const sorted = [...filtered].sort((a, b) => {
                                                                if (a.result !== b.result) return a.result === 'VIOLATION' ? -1 : 1;
                                                                if (a.result === 'VIOLATION') {
                                                                    const sa = severityRank[a.severity] || 0;
                                                                    const sb = severityRank[b.severity] || 0;
                                                                    if (sa !== sb) return sb - sa;
                                                                }
                                                                const ra = (a.resource_id || '').toString();
                                                                const rb = (b.resource_id || '').toString();
                                                                if (ra !== rb) return ra.localeCompare(rb);
                                                                return (a.policy || '').localeCompare(b.policy || '');
                                                            });

                                                            if (sorted.length === 0) {
                                                                return (
                                                                    <div className="flex flex-col items-center justify-center h-full p-12 text-center">
                                                                        <CheckCircle2 className="w-12 h-12 text-green-400 mb-4" />
                                                                        <h3 className="text-lg font-medium text-gray-900">Nothing matches this filter</h3>
                                                                        <p className="text-gray-500 text-sm mt-1 max-w-sm">
                                                                            Try switching the filter above to see other checks.
                                                                        </p>
                                                                    </div>
                                                                );
                                                            }

                                                            return (
                                                                <table className="w-full text-sm">
                                                                    <thead className="bg-white sticky top-0 z-10 text-gray-500 font-medium border-b border-gray-200">
                                                                        <tr>
                                                                            <th className="p-3 px-4 text-left w-28">Result</th>
                                                                            <th className="p-3 text-left">Resource</th>
                                                                            <th className="p-3 text-left">Policy</th>
                                                                            <th className="p-3 text-left w-24">Severity</th>
                                                                            <th className="p-3 text-left w-2/5">Notes</th>
                                                                        </tr>
                                                                    </thead>
                                                                    <tbody className="divide-y divide-gray-100">
                                                                        {sorted.map((c: any, idx: number) => {
                                                                            const tags = c.resource?.tags;
                                                                            const tagList = Array.isArray(tags)
                                                                                ? tags
                                                                                : (tags && typeof tags === 'object'
                                                                                    ? Object.entries(tags).map(([k, v]) => `${k}: ${v}`)
                                                                                    : []);
                                                                            return (
                                                                                <tr key={idx} className="hover:bg-gray-50/60 align-top">
                                                                                    <td className="p-3 px-4">
                                                                                        {c.result === 'PASS' ? (
                                                                                            <span className="inline-flex items-center gap-1 text-[10px] uppercase font-bold px-2 py-1 rounded-full bg-green-50 text-green-700 border border-green-200">
                                                                                                <CheckCircle2 className="w-3 h-3" /> Pass
                                                                                            </span>
                                                                                        ) : (
                                                                                            <span className="inline-flex items-center gap-1 text-[10px] uppercase font-bold px-2 py-1 rounded-full bg-red-50 text-red-700 border border-red-200">
                                                                                                <XCircle className="w-3 h-3" /> Violation
                                                                                            </span>
                                                                                        )}
                                                                                    </td>
                                                                                    <td className="p-3">
                                                                                        <div className="flex flex-col gap-1">
                                                                                            <span className="text-[10px] text-gray-700 font-semibold uppercase tracking-wider">{c.resource_type}</span>
                                                                                            <span className="font-medium text-gray-900">{c.resource?.name || c.resource_id}</span>
                                                                                            <span className="font-mono text-[10px] text-gray-500 break-all">{c.resource_id}</span>
                                                                                            {tagList.length > 0 && (
                                                                                                <div className="flex flex-wrap gap-1 mt-1">
                                                                                                    {tagList.slice(0, 4).map((t: string, i: number) => (
                                                                                                        <span key={i} className="inline-block text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-600 border border-gray-200">{t}</span>
                                                                                                    ))}
                                                                                                    {tagList.length > 4 && (
                                                                                                        <span className="inline-block text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-500 border border-gray-200">+{tagList.length - 4}</span>
                                                                                                    )}
                                                                                                </div>
                                                                                            )}
                                                                                        </div>
                                                                                    </td>
                                                                                    <td className="p-3 text-gray-700">{(c.policy || '').replace(/_/g, ' ')}</td>
                                                                                    <td className="p-3">
                                                                                        {c.result === 'VIOLATION' ? (
                                                                                            <span className={`text-[10px] uppercase font-bold px-2 py-1 rounded-full ${
                                                                                                c.severity === 'CRITICAL' ? 'bg-red-100 text-red-800 border border-red-200' :
                                                                                                c.severity === 'HIGH' ? 'bg-orange-100 text-orange-800 border border-orange-200' :
                                                                                                c.severity === 'MEDIUM' ? 'bg-yellow-100 text-yellow-800 border border-yellow-200' :
                                                                                                'bg-gray-100 text-gray-800 border border-gray-200'
                                                                                            }`}>
                                                                                                {c.severity}
                                                                                            </span>
                                                                                        ) : (
                                                                                            <span className="text-gray-300 text-xs">—</span>
                                                                                        )}
                                                                                    </td>
                                                                                    <td className="p-3 text-xs text-gray-600 leading-relaxed break-words">
                                                                                        {Array.isArray(c.rule_results) && c.rule_results.length > 0 ? (
                                                                                            <ul className="space-y-1.5">
                                                                                                {c.rule_results.map((rr: any) => (
                                                                                                    <li key={rr.id} className="flex items-start gap-2">
                                                                                                        {rr.passed ? (
                                                                                                            <CheckCircle2 className="w-3.5 h-3.5 text-green-500 mt-0.5 flex-shrink-0" />
                                                                                                        ) : (
                                                                                                            <XCircle className="w-3.5 h-3.5 text-red-500 mt-0.5 flex-shrink-0" />
                                                                                                        )}
                                                                                                        <div className="flex-1 min-w-0">
                                                                                                            <span className={rr.passed ? 'text-gray-700' : 'text-red-700 font-medium'}>
                                                                                                                {rr.description}
                                                                                                            </span>
                                                                                                            {(() => {
                                                                                                                // Rego emits `messages`; fall back to `violations`
                                                                                                                // for any legacy in-flight runs.
                                                                                                                const msgs: string[] = (rr.messages || rr.violations || []) as string[];
                                                                                                                if (rr.passed || msgs.length === 0) return null;
                                                                                                                return (
                                                                                                                    <ul className="mt-1 ml-1 space-y-0.5 text-[11px] text-gray-500">
                                                                                                                        {msgs.slice(0, 3).map((v, vi) => (
                                                                                                                            <li key={vi}>• {v}</li>
                                                                                                                        ))}
                                                                                                                        {msgs.length > 3 && (
                                                                                                                            <li className="italic">+{msgs.length - 3} more</li>
                                                                                                                        )}
                                                                                                                    </ul>
                                                                                                                );
                                                                                                            })()}
                                                                                                        </div>
                                                                                                    </li>
                                                                                                ))}
                                                                                            </ul>
                                                                                        ) : c.result === 'VIOLATION' ? (
                                                                                            formatReason(c)
                                                                                        ) : c.resource?.description ? (
                                                                                            <span className="italic text-gray-500">{c.resource.description}</span>
                                                                                        ) : (
                                                                                            <span className="text-gray-300">—</span>
                                                                                        )}
                                                                                    </td>
                                                                                </tr>
                                                                            );
                                                                        })}
                                                                    </tbody>
                                                                </table>
                                                            );
                                                        })()}
                                                    </div>
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
                                <div><span className="font-semibold text-gray-500 block mb-1">Resource Type</span> {selectedViolation.resource_type}</div>
                                <div><span className="font-semibold text-gray-500 block mb-1">Policy</span> {selectedViolation.policy}</div>
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
                                <div className="col-span-2"><span className="font-semibold text-gray-500 block mb-1">Reason</span> <div className="mt-2 text-gray-700 leading-relaxed">{formatReason(selectedViolation)}</div></div>
                            </div>
                            
                            {selectedViolation.input_context && (
                                <div>
                                    <h4 className="text-sm font-semibold text-gray-900 mb-2">OPA Input Parameters</h4>
                                    <div className="text-xs text-gray-500 mb-2">The exact data payload sent to Open Policy Agent (Rego) during the evaluation.</div>
                                    <pre className="bg-gray-900 p-4 rounded-lg border border-gray-700 text-xs font-mono text-blue-400 overflow-x-auto whitespace-pre-wrap break-words shadow-inner">
                                        {JSON.stringify(selectedViolation.input_context, null, 2)}
                                    </pre>
                                </div>
                            )}
                            
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
        </div>
    );
}
