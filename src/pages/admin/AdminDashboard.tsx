
import { useState, useMemo } from 'react';
import { useRequestStore } from '../../stores/requestStore';
import { Card, CardContent, CardHeader, CardTitle } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import {
    Activity, CheckCircle2, FileStack, TrendingUp, Clock, AlertTriangle,
    XCircle, Timer, Loader2, DollarSign,
    Search, ChevronUp, ChevronDown, ChevronRight, Filter, SlidersHorizontal, CheckSquare, Square, MessageCircle
} from 'lucide-react';
import { format, subDays, isAfter, differenceInHours, differenceInMinutes, parseISO } from 'date-fns';
import { RequestDetailsModal } from '../../components/RequestDetailsModal';
import { RequestStateList } from '../Requests';
import type { Request } from '../../types';

// Manual effort estimates in hours
const EFFORT_ESTIMATES: Record<string, number> = {
    'workspace_provision': 4,
    'workspace_access': 0.5,
    'catalog_schema_table': 1,
    'service_principal': 2,
    'marketplace_certification': 8,
    'github_repo_creation': 0.5,
    'default': 1
};

export function AdminDashboard() {
    const requests = useRequestStore((state) => state.requests);
    const getPendingApprovalsCount = useRequestStore((state) => state.getPendingApprovalsCount);

    // --- Metrics Calculation ---
    const metrics = useMemo(() => {
        const total = requests.length;
        const pendingApprovals = getPendingApprovalsCount();
        const active = requests.filter(r => r.type === 'workspace_provision' && r.status === 'completed').length;
        const newRequests = requests.filter(r => isAfter(parseISO(r.createdAt), subDays(new Date(), 1))).length;

        // New Metrics
        const failed24h = requests.filter(r => r.status === 'failed' && isAfter(parseISO(r.updatedAt), subDays(new Date(), 1))).length;
        const provisioning = requests.filter(r => r.status === 'provisioning').length;
        const rejected = requests.filter(r => r.status === 'rejected').length;

        // Stuck Requests (> 3 days in same state)
        const stuck = requests.filter(r => {
            if (['completed', 'rejected', 'failed'].includes(r.status)) return false;
            const activeState = r.stateMachine?.states?.find(s => s.isActive);
            if (!activeState?.startedAt) return false;
            const durationHours = differenceInHours(new Date(), parseISO(activeState.startedAt));
            return durationHours > 72;
        }).length;

        // Avg Completion Time
        const completedReqs = requests.filter(r => r.status === 'completed');
        const avgCompletionHours = completedReqs.length > 0
            ? completedReqs.reduce((acc, r) => {
                return acc + differenceInHours(parseISO(r.updatedAt), parseISO(r.createdAt));
            }, 0) / completedReqs.length
            : 0;

        // Pending My Action
        const myPending = requests.filter(r => {
            // This is an approximation. Real logic requires detailed approval data which might not be fully available 
            // on the request object depending on backend response. 
            // Assuming we can filter by 'pending' and check if current user is the target.
            // For now, simpler: Just show total pending approvals (revisit if we have specific user assignment data)
            return r.status === 'manager_approval' || r.status.includes('approval');
        }).length;

        // Labor Hours Saved
        const laborHours = requests.reduce((acc, r) => {
            if (r.status === 'completed') {
                const estimate = EFFORT_ESTIMATES[r.type] || EFFORT_ESTIMATES['default'];
                return acc + estimate;
            }
            return acc;
        }, 0);

        return { total, pendingApprovals, active, newRequests, failed24h, provisioning, rejected, stuck, avgCompletionHours, myPending, laborHours };
    }, [requests, getPendingApprovalsCount]);

    // --- Search, Filter & Sort State ---
    const [searchQuery, setSearchQuery] = useState('');
    const [sortConfig, setSortConfig] = useState<{ key: string; direction: 'asc' | 'desc' } | null>({ key: 'createdAt', direction: 'desc' });
    const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set());
    const [visibleColumns, setVisibleColumns] = useState<Set<string>>(new Set(['id', 'title', 'type', 'currentState', 'timeInState', 'actions']));
    const [filters, setFilters] = useState<Record<string, string>>({});
    const [showColumnSelector, setShowColumnSelector] = useState(false);
    const [showFilters, setShowFilters] = useState(false);
    const [selectedConversationRequest, setSelectedConversationRequest] = useState<Request | null>(null);

    const toggleColumn = (columnId: string) => {
        const newSet = new Set(visibleColumns);
        if (newSet.has(columnId)) {
            if (newSet.size > 1) newSet.delete(columnId);
        } else {
            newSet.add(columnId);
        }
        setVisibleColumns(newSet);
    };

    const COLUMNS = [
        { id: 'id', label: 'ID' },
        { id: 'title', label: 'Title' },
        { id: 'type', label: 'Type' },
        { id: 'currentState', label: 'Current State' },
        { id: 'requester_email', label: 'Requested By' },
        { id: 'timeInState', label: 'Time in State' },
        { id: 'createdAt', label: 'Created' },
        { id: 'actions', label: 'Actions' },
    ];


    // --- Helper Functions ---
    const toggleRow = (id: string) => {
        const newSet = new Set(expandedRows);
        if (newSet.has(id)) newSet.delete(id);
        else newSet.add(id);
        setExpandedRows(newSet);
    };

    const getCurrentStateName = (request: Request) => {
        if (request.status === 'failed') return 'Failed';
        if (request.status === 'rejected') return 'Rejected';
        if (request.status === 'completed') return 'Completed';
        const activeState = request.stateMachine?.states?.find(s => s.isActive);
        return activeState ? activeState.name : '-';
    };

    const getTimeInState = (request: Request) => {
        const activeState = request.stateMachine?.states?.find(s => s.isActive);
        if (!activeState?.startedAt) return 0;
        return differenceInMinutes(new Date(), parseISO(activeState.startedAt));
    };



    const formatDuration = (minutes: number) => {
        if (minutes < 60) return `${minutes}m`;
        const hours = Math.floor(minutes / 60);
        if (hours < 24) return `${hours}h ${minutes % 60}m`;
        const days = Math.floor(hours / 24);
        return `${days}d ${hours % 24}h`;
    };

    const handleSort = (key: string) => {
        let direction: 'asc' | 'desc' = 'asc';
        if (sortConfig && sortConfig.key === key && sortConfig.direction === 'asc') direction = 'desc';
        setSortConfig({ key, direction });
    };

    // --- Filtering & Sorting Logic ---
    const filteredAndSortedRequests = useMemo(() => {
        const result = requests.filter(r => {
            const matchesSearch =
                r.id.toLowerCase().includes(searchQuery.toLowerCase()) ||
                r.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
                r.requester_email?.toLowerCase().includes(searchQuery.toLowerCase());

            if (!matchesSearch) return false;

            // Apply column filters
            for (const [key, value] of Object.entries(filters)) {
                if (!value) continue;
                let rValue = '';
                if (key === 'currentState') {
                    rValue = getCurrentStateName(r).toLowerCase();
                } else {
                    rValue = String((r as unknown as Record<string, unknown>)[key] || '').toLowerCase();
                }
                if (!rValue.includes(value.toLowerCase())) return false;
            }
            return true;
        });

        if (sortConfig) {
            result.sort((a, b) => {
                let aValue: string | number = (a as unknown as Record<string, string | number>)[sortConfig.key];
                let bValue: string | number = (b as unknown as Record<string, string | number>)[sortConfig.key];

                if (sortConfig.key === 'timeInState') {
                    aValue = getTimeInState(a);
                    bValue = getTimeInState(b);
                } else if (sortConfig.key === 'currentState') {
                    aValue = getCurrentStateName(a);
                    bValue = getCurrentStateName(b);
                } else if (sortConfig.key === 'createdAt' || sortConfig.key === 'updatedAt') {
                    aValue = new Date(aValue).getTime();
                    bValue = new Date(bValue).getTime();
                }

                if (aValue < bValue) return sortConfig.direction === 'asc' ? -1 : 1;
                if (aValue > bValue) return sortConfig.direction === 'asc' ? 1 : -1;
                return 0;
            });
        }
        return result;
    }, [requests, searchQuery, filters, sortConfig]);

    return (
        <div className="space-y-6">
            {/* Metrics Grid */}
            <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
                <MetricCard icon={FileStack} label="Total Requests" value={metrics.total} color="blue" />
                <MetricCard icon={Clock} label="Pending Approvals" value={metrics.pendingApprovals} color="amber" />
                <MetricCard icon={CheckCircle2} label="Active Workspaces" value={metrics.active} color="green" />
                <MetricCard icon={TrendingUp} label="New (24h)" value={metrics.newRequests} color="purple" />
                <MetricCard icon={Timer} label="Avg Completion" value={`${metrics.avgCompletionHours.toFixed(1)}h`} color="indigo" />
                <MetricCard icon={AlertTriangle} label="Stuck (>3d)" value={metrics.stuck} color="orange" />
                <MetricCard icon={XCircle} label="Failed (24h)" value={metrics.failed24h} color="red" />
                <MetricCard icon={Loader2} label="Provisioning" value={metrics.provisioning} color="cyan" />
                <MetricCard icon={XCircle} label="Rejected" value={metrics.rejected} color="gray" />
                <MetricCard icon={DollarSign} label="Labor Saved" value={`${metrics.laborHours}h`} color="emerald" />
            </div>

            {/* Main Table Card */}
            <Card className="flex flex-col h-[600px]"> {/* Fixed height for sticky scroll */}
                <CardHeader className="py-4 border-b border-gray-200">
                    <div className="flex items-center justify-between">
                        <CardTitle className="text-lg flex items-center gap-2">
                            <Activity className="w-5 h-5" /> All Requests
                        </CardTitle>
                        <div className="flex items-center gap-2">
                            <div className="relative">
                                <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-500" />
                                <Input
                                    placeholder="Search..."
                                    value={searchQuery}
                                    onChange={(e) => setSearchQuery(e.target.value)}
                                    className="pl-8 w-64 h-9"
                                />
                            </div>

                            <Button
                                variant={showFilters ? "default" : "outline"}
                                size="sm"
                                className="h-9 gap-1"
                                onClick={() => setShowFilters(!showFilters)}
                            >
                                <Filter className="w-4 h-4" /> Filter
                            </Button>

                            <div className="relative">
                                <Button
                                    variant="outline"
                                    size="sm"
                                    className="h-9 gap-1"
                                    onClick={() => setShowColumnSelector(!showColumnSelector)}
                                >
                                    <SlidersHorizontal className="w-4 h-4" /> Columns
                                </Button>
                                {showColumnSelector && (
                                    <div className="absolute right-0 top-full mt-2 w-48 bg-white border border-gray-200 rounded-lg shadow-lg z-20 p-2 space-y-1">
                                        <div className="text-xs font-semibold text-gray-500 uppercase px-2 mb-1">Visible Columns</div>
                                        {COLUMNS.map(col => (
                                            <div
                                                key={col.id}
                                                className="flex items-center gap-2 px-2 py-1.5 hover:bg-gray-50 rounded cursor-pointer text-sm"
                                                onClick={() => toggleColumn(col.id)}
                                            >
                                                {visibleColumns.has(col.id) ? <CheckSquare className="w-4 h-4 text-primary" /> : <Square className="w-4 h-4 text-gray-300" />}
                                                <span>{col.label}</span>
                                            </div>
                                        ))}
                                    </div>
                                )}
                            </div>
                        </div>
                    </div>
                </CardHeader>
                <CardContent className="p-0 flex-1 overflow-auto relative">
                    <table className="w-full text-sm text-left">
                        <thead className="bg-gray-50 text-gray-900 font-medium sticky top-0 z-10 shadow-sm">
                            <tr>
                                <th className="w-8 p-3"></th>{/* Expand Toggle */}
                                {visibleColumns.has('id') && <SortableHeader label="ID" sortKey="id" currentSort={sortConfig} onSort={handleSort} />}
                                {visibleColumns.has('title') && <SortableHeader label="Title" sortKey="title" currentSort={sortConfig} onSort={handleSort} />}
                                {visibleColumns.has('type') && <SortableHeader label="Type" sortKey="type" currentSort={sortConfig} onSort={handleSort} />}
                                {visibleColumns.has('currentState') && <SortableHeader label="Current State" sortKey="currentState" currentSort={sortConfig} onSort={handleSort} />}
                                {visibleColumns.has('requester_email') && <SortableHeader label="Requested By" sortKey="requester_email" currentSort={sortConfig} onSort={handleSort} />}
                                {visibleColumns.has('timeInState') && <SortableHeader label="Time in State" sortKey="timeInState" currentSort={sortConfig} onSort={handleSort} />}
                                {visibleColumns.has('createdAt') && <SortableHeader label="Created" sortKey="createdAt" currentSort={sortConfig} onSort={handleSort} />}
                                {visibleColumns.has('actions') && <th className="p-3">Actions</th>}
                            </tr>
                            {/* Filter Row */}
                            {showFilters && (
                                <tr className="bg-gray-100/50">
                                    <th className="p-2"></th>
                                    {visibleColumns.has('id') && <th className="p-2"><Input className="h-7 text-xs" placeholder="Filter ID..." value={filters['id'] || ''} onChange={(e) => setFilters(prev => ({ ...prev, id: e.target.value }))} /></th>}
                                    {visibleColumns.has('title') && <th className="p-2"><Input className="h-7 text-xs" placeholder="Filter Title..." value={filters['title'] || ''} onChange={(e) => setFilters(prev => ({ ...prev, title: e.target.value }))} /></th>}
                                    {visibleColumns.has('type') && <th className="p-2"><Input className="h-7 text-xs" placeholder="Filter Type..." value={filters['type'] || ''} onChange={(e) => setFilters(prev => ({ ...prev, type: e.target.value }))} /></th>}
                                    {visibleColumns.has('currentState') && <th className="p-2"><Input className="h-7 text-xs" placeholder="Filter State..." value={filters['currentState'] || ''} onChange={(e) => setFilters(prev => ({ ...prev, currentState: e.target.value }))} /></th>}
                                    {visibleColumns.has('requester_email') && <th className="p-2"><Input className="h-7 text-xs" placeholder="Filter User..." value={filters['requester_email'] || ''} onChange={(e) => setFilters(prev => ({ ...prev, requester_email: e.target.value }))} /></th>}
                                    {visibleColumns.has('timeInState') && <th className="p-2"></th>}
                                    {visibleColumns.has('createdAt') && <th className="p-2"></th>}
                                    {visibleColumns.has('actions') && <th className="p-2"></th>}
                                </tr>
                            )}

                        </thead>
                        <tbody className="divide-y divide-gray-100">
                            {filteredAndSortedRequests.map(req => (
                                <>
                                    <tr key={req.id} className="hover:bg-gray-50 group transition-colors">
                                        <td className="p-3">
                                            <button onClick={() => toggleRow(req.id)} className="p-1 hover:bg-gray-200 rounded">
                                                {expandedRows.has(req.id) ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                                            </button>
                                        </td>
                                        {visibleColumns.has('id') && <td className="p-3 font-mono text-xs">{req.id.slice(0, 8)}...</td>}
                                        {visibleColumns.has('title') && <td className="p-3 font-medium">{req.title}</td>}
                                        {visibleColumns.has('type') && <td className="p-3 text-gray-500">{req.type.replace(/_/g, ' ')}</td>}
                                        {visibleColumns.has('currentState') && <td className="p-3 text-xs font-medium text-gray-600 bg-gray-50/50 rounded-sm">
                                            {getCurrentStateName(req)}
                                        </td>}
                                        {visibleColumns.has('requester_email') && <td className="p-3 text-gray-600">{req.requester_email}</td>}
                                        {visibleColumns.has('timeInState') && <td className="p-3 font-mono">
                                            {['completed', 'rejected', 'failed'].includes(req.status) ? '-' : formatDuration(getTimeInState(req))}
                                        </td>}
                                        {visibleColumns.has('createdAt') && <td className="p-3 text-gray-500">{format(parseISO(req.createdAt), 'MMM d, HH:mm')}</td>}
                                        {visibleColumns.has('actions') && <td className="p-3">
                                            {req.conversation && req.conversation.length > 0 && (
                                                <button
                                                    onClick={() => setSelectedConversationRequest(req)}
                                                    className="text-blue-600 hover:text-blue-800 flex items-center gap-1 text-xs hover:underline"
                                                >
                                                    <MessageCircle className="w-3 h-3" /> Conv
                                                </button>
                                            )}
                                        </td>}
                                    </tr>
                                    {expandedRows.has(req.id) && (
                                        <tr className="bg-gray-50/50">
                                            <td colSpan={visibleColumns.size + 1} className="p-4 border-b border-gray-100 shadow-inner">
                                                <div className="space-y-2">
                                                    <h4 className="font-semibold text-xs uppercase tracking-wider text-gray-500">Request Details</h4>
                                                    <div className="grid grid-cols-2 gap-4 text-sm">
                                                        <div>
                                                            <span className="text-gray-500">Full ID:</span> <span className="font-mono">{req.id}</span>
                                                        </div>
                                                        <div>
                                                            <span className="text-gray-500">Environment:</span> {req.environment || 'N/A'}
                                                        </div>
                                                        {/* Add more details here */}
                                                    </div>
                                                    <div className="mt-4">
                                                        <h4 className="font-semibold text-xs uppercase tracking-wider text-gray-500 mb-2">State Machine</h4>
                                                        <div className="flex gap-2 overflow-x-auto pb-2">
                                                            {req.stateMachine?.states.map(s => (
                                                                <div key={s.id} className={`shrink-0 px-3 py-1.5 rounded-full text-xs border ${s.isActive ? 'bg-blue-100 border-blue-300 text-blue-800 font-medium' :
                                                                    s.isCompleted ? 'bg-green-50 border-green-200 text-green-700' :
                                                                        'bg-gray-100 border-gray-200 text-gray-400'
                                                                    }`}>
                                                                    {s.name}
                                                                </div>
                                                            ))}
                                                        </div>
                                                    </div>
                                                </div>
                                            </td>
                                        </tr>
                                    )}
                                </>
                            ))}
                        </tbody>
                    </table>
                </CardContent>
            </Card>

            {/* Request Details Modal (Pattern copied from Approvals.tsx) */}
            {selectedConversationRequest && (
                <RequestDetailsModal
                    request={selectedConversationRequest}
                    onClose={() => setSelectedConversationRequest(null)}
                    RequestStateList={RequestStateList}
                />
            )}
        </div >
    );
}

const METRIC_COLORS: Record<string, string> = {
    blue: 'bg-blue-50 text-blue-700',
    amber: 'bg-amber-50 text-amber-700',
    green: 'bg-green-50 text-green-700',
    purple: 'bg-purple-50 text-purple-700',
    indigo: 'bg-indigo-50 text-indigo-700',
    orange: 'bg-orange-50 text-orange-700',
    red: 'bg-red-50 text-red-700',
    cyan: 'bg-cyan-50 text-cyan-700',
    gray: 'bg-gray-50 text-gray-700',
    emerald: 'bg-emerald-50 text-emerald-700',
};

function MetricCard({ icon: Icon, label, value, color }: any) {
    const bgClass = METRIC_COLORS[color] || 'bg-gray-50 text-gray-700';

    return (
        <Card className="border shadow-sm">
            <CardContent className="p-4 flex items-center justify-between">
                <div>
                    <p className="text-xs font-medium text-gray-500 uppercase tracking-wider">{label}</p>
                    <p className="text-xl font-bold mt-1">{value}</p>
                </div>
                <div className={`p-2 rounded-lg ${bgClass} bg-opacity-20`}>
                    <Icon className={`w-5 h-5 ${bgClass.split(' ')[1]}`} />
                </div>
            </CardContent>
        </Card>
    );
}

function SortableHeader({ label, sortKey, currentSort, onSort }: any) {
    return (
        <th className="p-3 cursor-pointer hover:bg-gray-100 transition-colors" onClick={() => onSort(sortKey)}>
            <div className="flex items-center gap-1">
                {label}
                <div className="flex flex-col">
                    <ChevronUp className={`w-3 h-3 -mb-1 ${currentSort?.key === sortKey && currentSort.direction === 'asc' ? 'text-gray-900' : 'text-gray-300'}`} />
                    <ChevronDown className={`w-3 h-3 ${currentSort?.key === sortKey && currentSort.direction === 'desc' ? 'text-gray-900' : 'text-gray-300'}`} />
                </div>
            </div>
        </th>
    );
}

