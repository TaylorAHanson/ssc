import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { ShieldAlert, AlertTriangle, Search, Unlock, Lock, CheckCircle2, Loader2, X, FileStack, ShieldCheck, ListChecks, ArrowRight } from 'lucide-react';
import { useRequestStore } from '../../stores/requestStore';
import { useState, useMemo, useEffect } from 'react';
import { format, parseISO } from 'date-fns';

export function EnforcementSentinel() {
    const addRequest = useRequestStore((state) => state.addRequest);
    const fetchRequests = useRequestStore((state) => state.fetchRequests);
    const requests = useRequestStore((state) => state.requests);
    const [isRunning, setIsRunning] = useState(false);
    const [isEnforcementUnlocked, setIsEnforcementUnlocked] = useState(false);
    const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
    const [activeTab, setActiveTab] = useState<string>('all');
    const [workspace, setWorkspace] = useState('ws-enterprise-prod');
    const [environment, setEnvironment] = useState<'dev' | 'stage' | 'prod'>('prod');

    // Filter to only get sentinel runs
    const sentinelRuns = requests
        .filter(r => r.type === 'enforcement_sentinel')
        .sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime());

    const selectedRun = useMemo(() => sentinelRuns.find(r => r.id === selectedRunId), [sentinelRuns, selectedRunId]);

    // Poll if any runs are actively running
    useEffect(() => {
        const hasActiveRuns = sentinelRuns.some(run => run.status !== 'completed' && run.status !== 'failed' && run.status !== 'rejected');
        
        if (hasActiveRuns) {
            const interval = setInterval(() => {
                fetchRequests();
            }, 3000);
            return () => clearInterval(interval);
        }
    }, [sentinelRuns, fetchRequests]);

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
            await addRequest('enforcement_sentinel', `Manual Sentinel Run (${modeLabel})`, environment, {
                enforcement_mode: mode,
                workspace: workspace,
                environment: environment
            });
            
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
                        <div className="flex items-center gap-2 text-sm text-gray-700 font-medium">
                            <Search className="w-4 h-4" />
                            <span>Manual Trigger</span>
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
                <CardHeader>
                    <CardTitle className="text-lg">Run History</CardTitle>
                    <CardDescription>View previous enforcement audits, their findings, and executed actions.</CardDescription>
                </CardHeader>
                <CardContent className="p-0">
                    <table className="w-full text-sm text-left">
                        <thead className="bg-gray-50 text-gray-900 font-medium border-b border-gray-200">
                            <tr>
                                <th className="p-3 pl-4">Run Date</th>
                                <th className="p-3">Mode</th>
                                <th className="p-3">Status</th>
                                <th className="p-3">Violations Found</th>
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
                                    
                                    const discoverFact = run.stateMachine?.states?.flatMap(s => s.facts || []).find(f => f.type === 'discover_completed');
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
                </CardContent>
            </Card>

            {/* Modal for run details */}
            {selectedRun && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4 animate-in fade-in">
                    <div className="bg-white rounded-xl shadow-xl w-full max-w-6xl max-h-[90vh] flex flex-col overflow-hidden animate-in slide-in-from-bottom-4">
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
                            <Button variant="ghost" size="sm" onClick={() => { setSelectedRunId(null); setActiveTab('all'); }} className="rounded-full hover:bg-gray-100">
                                <X className="w-5 h-5 text-gray-500" />
                            </Button>
                        </div>

                        {(() => {
                            const ctx = (selectedRun as any).stateContext || selectedRun.metadata || (selectedRun as any).state_context || {};
                            const violations: any[] = ctx.violations || [];
                            const discoverFact = selectedRun.stateMachine?.states?.flatMap((s: any) => s.facts || []).find((f: any) => f.type === 'discover_completed');
                            
                            const assetsScanned = discoverFact?.data?.total_resources_scanned ?? '—';
                            const policiesEvaluated = discoverFact?.data?.policies_evaluated ?? '—';
                            const totalChecks = discoverFact?.data?.total_checks ?? '—';
                            const vCount = discoverFact?.data?.violation_count ?? violations.length;

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
                                    {selectedRun.status !== 'completed' && selectedRun.status !== 'failed' && selectedRun.status !== 'rejected' ? (
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
                                                <Card className={`shadow-sm border-gray-200 ${vCount > 0 ? 'bg-red-50/50 border-red-100' : 'bg-green-50/50 border-green-100'}`}>
                                                    <CardContent className="p-4 flex flex-col gap-1">
                                                        <div className="flex items-center text-sm font-medium text-gray-500 mb-1">
                                                            <AlertTriangle className={`w-4 h-4 mr-2 ${vCount > 0 ? 'text-red-500' : 'text-green-500'}`} /> Violations
                                                        </div>
                                                        <div className={`text-2xl font-bold ${vCount > 0 ? 'text-red-600' : 'text-green-600'}`}>{vCount}</div>
                                                    </CardContent>
                                                </Card>
                                            </div>

                                            {/* Detailed Report Section */}
                                            <div className="bg-white border border-gray-200 rounded-lg shadow-sm flex flex-col overflow-hidden min-h-[400px]">
                                                {/* Tabs */}
                                                <div className="flex overflow-x-auto border-b border-gray-100 bg-gray-50/80 p-2 gap-2 hide-scrollbar">
                                                    <button
                                                        onClick={() => setActiveTab('all')}
                                                        className={`px-3 py-1.5 text-sm font-medium rounded-md whitespace-nowrap transition-colors ${
                                                            activeTab === 'all' 
                                                            ? 'bg-white text-gray-900 shadow-sm ring-1 ring-gray-200' 
                                                            : 'text-gray-500 hover:text-gray-900 hover:bg-gray-100'
                                                        }`}
                                                    >
                                                        All Violations ({violations.length})
                                                    </button>
                                                    {policyGroups.map(policy => (
                                                        <button
                                                            key={policy}
                                                            onClick={() => setActiveTab(policy)}
                                                            className={`px-3 py-1.5 text-sm font-medium rounded-md whitespace-nowrap transition-colors ${
                                                                activeTab === policy 
                                                                ? 'bg-white text-gray-900 shadow-sm ring-1 ring-gray-200' 
                                                                : 'text-gray-500 hover:text-gray-900 hover:bg-gray-100'
                                                            }`}
                                                        >
                                                            {policy.replace(/_/g, ' ')} ({violationsByPolicy[policy].length})
                                                        </button>
                                                    ))}
                                                </div>

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
                                                                </tr>
                                                            </thead>
                                                            <tbody className="divide-y divide-gray-100">
                                                                {activeViolations.map((v: any, idx: number) => (
                                                                    <tr key={idx} className="hover:bg-gray-50">
                                                                        <td className="p-3 px-4 font-mono text-xs text-gray-900">
                                                                            <div className="flex flex-col">
                                                                                <span className="text-[10px] text-gray-400 uppercase tracking-wider font-sans mb-0.5">{v.resource_type}</span>
                                                                                {v.resource_id}
                                                                            </div>
                                                                        </td>
                                                                        {activeTab === 'all' && <td className="p-3 text-gray-600">{v.policy.replace(/_/g, ' ')}</td>}
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
                                                                        <td className="p-3 text-xs text-gray-600 break-words leading-relaxed">{v.reason}</td>
                                                                    </tr>
                                                                ))}
                                                            </tbody>
                                                        </table>
                                                    )}
                                                </div>
                                            </div>
                                        </>
                                    )}
                                </div>
                            );
                        })()}
                    </div>
                </div>
            )}
        </div>
    );
}
