import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { AlertCircle, Calendar, CheckCircle2, ListChecks, Loader2, RefreshCw, Search, X } from 'lucide-react';
import { useRequestStore } from '../stores/requestStore';
import { useUserStore } from '../stores/userStore';
import { useBrandingStore } from '../stores/brandingStore';
import { GUIDE_APPROVALS, GUIDE_REQUESTS, useGuideEnabled } from '../lib/guide';
import { useVisibleInterval } from '../hooks/useVisibleInterval';
import { cn } from '../lib/utils';
import { api, getRequest } from '../services/api';
import { Button } from '../components/ui/button';
import { RequestDetailsModal } from '../components/RequestDetailsModal';
import { parseUtcDate } from '../lib/requestDates';
import { NewRequestBanner } from '../components/requests/NewRequestBanner';
import { DelegationForm, DelegationStrips } from '../components/requests/DelegationPanel';
import { ApprovalActionDialog, BulkApproveDialog, type ApprovalDialogAction } from '../components/requests/ApprovalDialogs';
import { ApprovalRow, RequestRow } from '../components/requests/RequestRows';
import {
  AGING_DAYS, BULK_APPROVE_MAX, SELF_APPROVAL_MESSAGE, approvalHaystack, approvalStatusMatches, isBulkCandidate,
  isOverdue, isRequestActive, requestHaystack, requestStatusMatches, typeLabel, waitingDays, workflowLabel,
  type ApprovalStatusFilter, type InboxApproval, type RequestStatusFilter, type View,
} from '../components/requests/rows';
import type { Delegation, Request } from '../types';

// Mirrors the backend list endpoint's default page; past it, older requests
// are reachable by deep link but not listed.
const REQUEST_LIST_CAP = 100;
// Rows rendered per "Show more", so a large inbox stays light to scroll and search.
const PAGE_SIZE = 100;

// Roles that approve something. Group-based approvers (no role) still get the
// tab as soon as anything is assigned to them.
const APPROVER_ROLES = new Set([
  'platform_admin', 'data_owner', 'manager', 'governance_admin', 'security_admin', 'finance_admin', 'security',
]);

const APPROVAL_STATUS_OPTIONS: { value: ApprovalStatusFilter; label: string }[] = [
  { value: 'pending', label: 'Waiting' },
  { value: 'done', label: 'Handled' },
  { value: 'all', label: 'All' },
];
const REQUEST_STATUS_OPTIONS: { value: RequestStatusFilter; label: string }[] = [
  { value: 'active', label: 'In progress' },
  { value: 'completed', label: 'Completed' },
  { value: 'failed', label: 'Failed / rejected' },
  { value: 'all', label: 'All' },
];

type ApprovalItem = { key: string; approval: InboxApproval; workflowKey: string; workflow: string; haystack: string };
type RequestItem = { key: string; request: Request; workflowKey: string; workflow: string; haystack: string };

const ts = (iso: string) => parseUtcDate(iso).getTime();

/**
 * Requests and approvals on one page. The URL picks the tab, so existing links
 * keep working: `/approvals` is the approver's queue, `/requests` the caller's
 * own requests, `/requests?view=all` every request (Platform Admins), and
 * `/requests/:id` opens one request over the list.
 */
export function Requests() {
  const guideEnabled = useGuideEnabled();
  const location = useLocation();
  const navigate = useNavigate();
  const { requestId } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();

  const requests = useRequestStore((state) => state.requests);
  const approvals = useRequestStore((state) => state.approvals) as InboxApproval[];
  const delegations = useRequestStore((state) => state.delegations);
  const fetchRequests = useRequestStore((state) => state.fetchRequests);
  const fetchApprovals = useRequestStore((state) => state.fetchApprovals);
  const fetchDelegations = useRequestStore((state) => state.fetchDelegations);
  const deleteRequest = useRequestStore((state) => state.deleteRequest);
  const currentUser = useUserStore((state) => state.currentUser);
  const uiTabs = useBrandingStore((state) => state.uiTabs);

  const userRoles = useMemo(
    () => currentUser?.roles.map((r) => r.toLowerCase().replace(/\s+/g, '_')) ?? [],
    [currentUser],
  );
  const isPlatformAdmin = userRoles.includes('platform_admin');

  const view: View = location.pathname.startsWith('/approvals')
    ? 'approvals'
    : isPlatformAdmin && searchParams.get('view') === 'all' ? 'all' : 'mine';

  // --- Data ------------------------------------------------------------------
  const [requestsLoaded, setRequestsLoaded] = useState(false);
  const [approvalsLoaded, setApprovalsLoaded] = useState(false);
  const [delegationsToMe, setDelegationsToMe] = useState<Delegation[]>([]);
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    let mounted = true;
    fetchRequests().finally(() => mounted && setRequestsLoaded(true));
    fetchApprovals().finally(() => mounted && setApprovalsLoaded(true));
    return () => {
      mounted = false;
    };
  }, [fetchRequests, fetchApprovals]);

  // A Platform Admin's main list is everyone's (and capped), so their own
  // requests are fetched on their own; for anyone else the main list is theirs.
  const [ownRequests, setOwnRequests] = useState<Request[] | null>(null);
  const fetchOwnRequests = useCallback(async () => {
    if (!isPlatformAdmin) return;
    try {
      setOwnRequests(await api.getRequests({ mine: true }));
    } catch {
      setOwnRequests((prev) => prev ?? []);
    }
  }, [isPlatformAdmin]);
  useEffect(() => {
    void fetchOwnRequests();
  }, [fetchOwnRequests]);

  const loadDelegations = useCallback(async () => {
    if (!currentUser?.email) return;
    await Promise.all([
      fetchDelegations(currentUser.email),
      api.getDelegations(undefined, currentUser.email).then(setDelegationsToMe).catch(() => setDelegationsToMe([])),
    ]);
  }, [currentUser?.email, fetchDelegations]);
  useEffect(() => {
    void loadDelegations();
  }, [loadDelegations]);

  // Poll the list on screen to catch state transitions (paused while the tab is hidden).
  useVisibleInterval(view === 'mine' && isPlatformAdmin ? fetchOwnRequests : fetchRequests, 10000, view !== 'approvals');
  useVisibleInterval(fetchApprovals, 30000, view === 'approvals');

  const refresh = async () => {
    setRefreshing(true);
    try {
      await Promise.all([fetchRequests(), fetchOwnRequests(), fetchApprovals(), loadDelegations()]);
    } finally {
      setRefreshing(false);
    }
  };

  // --- Tabs ------------------------------------------------------------------
  // No roles explainer: if you can act on something, it's in this tab with its
  // buttons. A tab an admin switched off stays reachable by its link (the
  // notifications bell always points at the approvals one).
  const isApprover =
    approvals.length > 0 || delegationsToMe.length > 0 || delegations.length > 0 ||
    userRoles.some((r) => APPROVER_ROLES.has(r));
  const pendingTotal = approvals.filter((a) => a.status === 'pending').length;
  const myRequests = useMemo(
    () => (isPlatformAdmin ? ownRequests ?? [] : requests),
    [requests, isPlatformAdmin, ownRequests],
  );
  const myActive = myRequests.filter(isRequestActive).length;
  const tabs = [
    {
      id: 'approvals' as const,
      label: 'Needs my approval',
      count: pendingTotal,
      show: (uiTabs?.pending_approvals !== false && isApprover) || view === 'approvals',
    },
    { id: 'mine' as const, label: 'My requests', count: myActive, show: uiTabs?.my_requests !== false || view === 'mine' },
    {
      id: 'all' as const,
      label: 'All requests',
      count: null,
      show: isPlatformAdmin && (uiTabs?.my_requests !== false || view === 'all'),
    },
  ].filter((t) => t.show);

  const goTo = (next: View) => {
    const params = new URLSearchParams();
    if (next === 'all') params.set('view', 'all');
    const wf = searchParams.get('workflow');
    if (wf) params.set('workflow', wf);
    const search = params.toString();
    navigate({ pathname: next === 'approvals' ? '/approvals' : '/requests', search: search ? `?${search}` : '' });
  };
  const listPath = view === 'all' ? '/requests?view=all' : '/requests';

  // --- Rows ------------------------------------------------------------------
  const workflowNames = useMemo(() => {
    const names = new Map<string, string>();
    for (const a of approvals) if (a.workflowName?.trim()) names.set(a.requestType, a.workflowName.trim());
    return names;
  }, [approvals]);

  const approvalItems = useMemo<ApprovalItem[]>(
    () => approvals.map((a) => ({
      key: a.id, approval: a, workflowKey: a.requestType, workflow: workflowLabel(a), haystack: approvalHaystack(a),
    })),
    [approvals],
  );
  const requestItems = useMemo<RequestItem[]>(() => {
    const source = view === 'all' ? requests : myRequests;
    return source.map((r) => {
      const workflow = workflowNames.get(r.type) ?? typeLabel(r.type);
      return { key: r.id, request: r, workflowKey: r.type, workflow, haystack: requestHaystack(r, workflow) };
    });
  }, [view, requests, myRequests, workflowNames]);

  // --- Filters ---------------------------------------------------------------
  const [query, setQuery] = useState('');
  const deferredQuery = useDeferredValue(query);
  const tokens = useMemo(() => deferredQuery.trim().toLowerCase().split(/\s+/).filter(Boolean), [deferredQuery]);
  const [approvalStatus, setApprovalStatus] = useState<ApprovalStatusFilter>('pending');
  // Until the user picks one: "In progress" when something is, else everything,
  // so the list doesn't open empty for someone with nothing in flight.
  const [pickedRequestStatus, setRequestStatus] = useState<RequestStatusFilter | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const workflowSelectRef = useRef<HTMLSelectElement>(null);

  // "/" jumps to search, like most list views.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (e.key !== '/' || e.metaKey || e.ctrlKey || e.altKey) return;
      if (target && (target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName))) return;
      e.preventDefault();
      searchRef.current?.focus();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // Workflow filter: ?workflow=<request type>, so a filtered list survives a
  // reload and can be shared. A value with no rows behind it shows everything.
  const baseItems: (ApprovalItem | RequestItem)[] = view === 'approvals' ? approvalItems : requestItems;
  const requestStatus: RequestStatusFilter =
    pickedRequestStatus ?? (requestItems.some((i) => isRequestActive(i.request)) ? 'active' : 'all');
  const workflowOptions = useMemo(() => {
    const byType = new Map<string, { value: string; label: string; count: number }>();
    for (const item of baseItems) {
      const option = byType.get(item.workflowKey) ?? { value: item.workflowKey, label: item.workflow, count: 0 };
      const counts = 'approval' in item ? item.approval.status === 'pending' : true;
      if (counts) option.count += 1;
      byType.set(item.workflowKey, option);
    }
    return [...byType.values()].sort((x, y) => x.label.localeCompare(y.label));
  }, [baseItems]);
  const workflowFilter = workflowOptions.find((o) => o.value === searchParams.get('workflow')) ?? null;
  const setWorkflowFilter = (value: string | null) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (value) next.set('workflow', value);
      else next.delete('workflow');
      return next;
    }, { replace: true });
  };

  const matched = useMemo(
    () => baseItems.filter((item) =>
      (!workflowFilter || item.workflowKey === workflowFilter.value) &&
      tokens.every((t) => item.haystack.includes(t))),
    [baseItems, workflowFilter, tokens],
  );

  const statusOptions = (view === 'approvals' ? APPROVAL_STATUS_OPTIONS : REQUEST_STATUS_OPTIONS).map((o) => ({
    ...o,
    count: matched.filter((item) =>
      'approval' in item
        ? approvalStatusMatches(item.approval, o.value as ApprovalStatusFilter)
        : requestStatusMatches(item.request, o.value as RequestStatusFilter)).length,
  }));
  const statusFilter = view === 'approvals' ? approvalStatus : requestStatus;
  const setStatusFilter = (value: string) =>
    view === 'approvals' ? setApprovalStatus(value as ApprovalStatusFilter) : setRequestStatus(value as RequestStatusFilter);

  const visible = useMemo(() => {
    if (view === 'approvals') {
      // Oldest pending first: the thing that has been blocking a requester the
      // longest is the thing to look at. Handled ones follow, newest first.
      const items = (matched as ApprovalItem[]).filter((i) => approvalStatusMatches(i.approval, approvalStatus));
      return items.sort((x, y) => {
        const xp = x.approval.status === 'pending';
        const yp = y.approval.status === 'pending';
        if (xp !== yp) return xp ? -1 : 1;
        return xp
          ? ts(x.approval.createdAt) - ts(y.approval.createdAt)
          : ts(y.approval.updatedAt) - ts(x.approval.updatedAt);
      });
    }
    return (matched as RequestItem[])
      .filter((i) => requestStatusMatches(i.request, requestStatus))
      .sort((x, y) => ts(y.request.createdAt) - ts(x.request.createdAt));
  }, [view, matched, approvalStatus, requestStatus]);

  const filterKey = `${view}|${statusFilter}|${tokens.join(' ')}|${workflowFilter?.value ?? ''}`;
  const [page, setPage] = useState({ key: '', limit: PAGE_SIZE });
  const limit = page.key === filterKey ? page.limit : PAGE_SIZE;
  const shown = visible.slice(0, limit);

  // One line above the queue so aging registers before you start clicking.
  const agingSummary = (() => {
    if (view !== 'approvals' || approvalStatus === 'done') return null;
    const pending = (matched as ApprovalItem[]).map((i) => i.approval).filter((a) => a.status === 'pending');
    const aging = pending.filter((a) => waitingDays(a.createdAt) >= AGING_DAYS);
    if (aging.length === 0) return null;
    const overdue = pending.filter((a) => a.dueAt && isOverdue(a.dueAt)).length;
    const manual = aging.filter((a) => a.approvalType === 'manual_task').length;
    const oldest = Math.max(...aging.map((a) => waitingDays(a.createdAt)));
    const parts = [
      `${aging.length} ${aging.length === 1 ? 'has' : 'have'} been waiting ${AGING_DAYS}+ days (oldest: ${oldest} days)`,
    ];
    if (manual > 0) {
      parts.push(`${manual} ${manual === 1 ? 'is a manual task' : 'are manual tasks'} — those requests stay paused until someone marks the work done`);
    }
    if (overdue > 0) parts.push(`${overdue} past ${overdue === 1 ? 'its' : 'their'} due date`);
    return `${parts.join('; ')}.`;
  })();

  // --- Expansion, selection, actions ----------------------------------------
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const toggleExpanded = (key: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  // Bulk approve covers only rows on screen: derived from what's shown, so rows
  // that stopped being pending, or that a filter hides, drop out.
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [bulkOpen, setBulkOpen] = useState(false);
  const bulkSelectable = view === 'approvals'
    ? (shown as ApprovalItem[]).map((i) => i.approval).filter((a) => isBulkCandidate(a) && !a.selfApprovalBlocked)
    : [];
  const hasBulkCandidates = view === 'approvals' && (shown as ApprovalItem[]).some((i) => isBulkCandidate(i.approval));
  const selectedApprovals = bulkSelectable.filter((a) => selectedIds.has(a.id));
  const allSelected = bulkSelectable.length > 0 && selectedApprovals.length === bulkSelectable.length;
  const someSelected = selectedApprovals.length > 0 && !allSelected;
  const tooManySelected = selectedApprovals.length > BULK_APPROVE_MAX;
  const toggleSelected = (id: string) =>
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const toggleSelectAll = () => setSelectedIds(allSelected ? new Set() : new Set(bulkSelectable.map((a) => a.id)));

  const [dialog, setDialog] = useState<{ approval: InboxApproval; action: ApprovalDialogAction } | null>(null);
  const [showDelegationForm, setShowDelegationForm] = useState(false);
  const [isDeleting, setIsDeleting] = useState<string | null>(null);

  const handleDelete = async (id: string) => {
    if (!window.confirm('Are you sure you want to delete this request? This action cannot be undone.')) return;
    setIsDeleting(id);
    try {
      await deleteRequest(id);
      if (isPlatformAdmin) setOwnRequests((prev) => prev?.filter((r) => r.id !== id) ?? prev);
    } finally {
      setIsDeleting(null);
    }
  };

  // Details from an approval row. An approver who isn't the requester (or a
  // Platform Admin) can't open the request itself, so say so instead of
  // silently doing nothing.
  // The store's list is polled by the details view, so prefer it for live updates.
  const findKnown = (id: string) => requests.find((r) => r.id === id) ?? ownRequests?.find((r) => r.id === id);
  const [inspect, setInspect] = useState<{ id: string; fetched?: Request } | null>(null);
  const [detailsError, setDetailsError] = useState<{ key: string; message: string } | null>(null);
  const openApprovalDetails = async (a: InboxApproval) => {
    setDetailsError(null);
    if (findKnown(a.requestId)) {
      setInspect({ id: a.requestId });
      return;
    }
    try {
      setInspect({ id: a.requestId, fetched: await getRequest(a.requestId) });
    } catch {
      setExpanded((prev) => new Set(prev).add(a.id));
      setDetailsError({
        key: a.id,
        message: "Only the requester and platform admins can open this request's full history; its parameters and reports are shown here.",
      });
    }
  };
  const inspected = inspect ? findKnown(inspect.id) ?? inspect.fetched ?? null : null;

  // Deep link /requests/:id. The list endpoint is capped, so a link to an older
  // request may not be in it; fetch that one directly.
  const listedLinked = requestId ? findKnown(requestId) : undefined;
  const [linkedRequest, setLinkedRequest] = useState<{ id: string; request: Request | null } | null>(null);
  const needsLookup = !!requestId && requestsLoaded && (!isPlatformAdmin || ownRequests !== null) && !listedLinked;
  useEffect(() => {
    if (!needsLookup || !requestId) return;
    let mounted = true;
    getRequest(requestId)
      .then((request) => { if (mounted) setLinkedRequest({ id: requestId, request }); })
      .catch(() => { if (mounted) setLinkedRequest({ id: requestId, request: null }); });
    return () => {
      mounted = false;
    };
  }, [needsLookup, requestId]);
  const lookupPending = needsLookup && linkedRequest?.id !== requestId;
  const linked = listedLinked ?? (linkedRequest && linkedRequest.id === requestId ? linkedRequest.request : null);
  const linkNotFound = !!requestId && requestsLoaded && !lookupPending && !linked;
  const closeLinked = useCallback(() => navigate(listPath), [navigate, listPath]);
  const closeInspect = useCallback(() => setInspect(null), []);

  // --- Render ----------------------------------------------------------------
  const loaded = view === 'approvals' ? approvalsLoaded
    : view === 'mine' && isPlatformAdmin ? ownRequests !== null
      : requestsLoaded;
  const noun = view === 'approvals' ? 'approvals' : 'requests';
  const colSpan = view === 'approvals' ? 7 : view === 'all' ? 7 : 6;
  const showApprovalsTab = tabs.some((t) => t.id === 'approvals');
  const subtitle =
    view === 'approvals' ? 'Requests waiting on your decision, and the ones you have handled.'
      : view === 'all' ? 'Every request, from every requester.'
        : "Track the status of every request you've submitted.";
  const statusLabel = statusOptions.find((o) => o.value === statusFilter)?.label.toLowerCase() ?? '';
  const totalForView = baseItems.length;

  const emptyState = (() => {
    if (!loaded) return null;
    if (totalForView === 0) {
      return view === 'approvals'
        ? { title: 'Nothing needs your approval', body: "You're all caught up!" }
        : view === 'all'
          ? { title: 'No requests yet', body: 'Requests appear here once anyone submits one.' }
          : { title: 'No requests yet', body: 'Once you submit a request through the agent, it will show up here.' };
    }
    if (visible.length > 0) return null;
    const others = statusOptions.find((o) => o.value === 'all')?.count ?? 0;
    const title = tokens.length > 0
      ? `No ${statusFilter === 'all' ? '' : `${statusLabel} `}${noun} match "${deferredQuery.trim()}"`
      : `No ${statusLabel} ${noun}${workflowFilter ? ` for ${workflowFilter.label}` : ''}`;
    const body = view === 'approvals' && approvalStatus === 'pending' && others === 0 && !tokens.length
      ? "You're all caught up!"
      : others > 0 && statusFilter !== 'all'
        ? `${others} ${others === 1 ? 'is' : 'are'} in other statuses.`
        : '';
    return { title, body };
  })();

  return (
    <div className="space-y-4 pb-20">
      {view !== 'approvals' && <NewRequestBanner />}

      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{showApprovalsTab ? 'Requests & Approvals' : 'My Requests'}</h1>
          <p className="text-sm text-gray-600">
            {subtitle}
            {guideEnabled && (
              <>
                {' · '}
                <Link to={view === 'approvals' ? GUIDE_APPROVALS : GUIDE_REQUESTS} className="text-accent hover:underline">
                  {view === 'approvals' ? 'How approvals work' : 'How requests work'}
                </Link>
              </>
            )}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {view === 'approvals' && (
            <Button
              size="sm"
              variant={showDelegationForm ? 'outline' : 'ghost'}
              onClick={() => setShowDelegationForm((v) => !v)}
              className="text-gray-700"
            >
              <Calendar className="w-4 h-4 mr-1.5" />
              Out of office
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={refresh} disabled={refreshing} title="Refresh" aria-label="Refresh" className="text-gray-600">
            <RefreshCw className={cn('w-4 h-4', refreshing && 'animate-spin')} />
          </Button>
        </div>
      </div>

      {tabs.length > 1 && (
        <div role="tablist" aria-label="Requests and approvals" className="flex gap-5 border-b border-gray-200">
          {tabs.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={view === t.id}
              onClick={() => view !== t.id && goTo(t.id)}
              className={cn(
                '-mb-px flex items-center gap-1.5 border-b-2 pb-2 text-sm font-medium transition-colors',
                view === t.id ? 'border-primary text-primary' : 'border-transparent text-gray-500 hover:text-gray-800',
              )}
            >
              {t.label}
              {t.count !== null && t.count > 0 && (
                <span
                  className={cn(
                    'min-w-[1.25rem] rounded-full px-1.5 text-[11px] font-semibold leading-5 tabular-nums',
                    t.id === 'approvals' ? 'bg-red-500 text-white' : 'bg-gray-100 text-gray-600',
                  )}
                  title={t.id === 'approvals' ? `${t.count} waiting on you` : `${t.count} in progress`}
                >
                  {t.count}
                </span>
              )}
            </button>
          ))}
        </div>
      )}

      {view === 'approvals' && (
        <>
          {showDelegationForm && <DelegationForm onClose={() => setShowDelegationForm(false)} />}
          <DelegationStrips incoming={delegationsToMe} />
        </>
      )}

      {linkNotFound && (
        <div role="alert" className="flex flex-wrap items-center gap-2 rounded-md border border-yellow-200 bg-yellow-50 px-3 py-2 text-sm text-yellow-900">
          <AlertCircle className="w-4 h-4 shrink-0" />
          <span>
            Request <strong className="font-mono">{requestId}</strong> could not be found. It may have been deleted,
            the database was reset, or it isn't yours to view.
          </span>
          <Button size="sm" variant="ghost" className="ml-auto h-7" onClick={closeLinked}>Dismiss</Button>
        </div>
      )}

      {/* Toolbar: search, status, workflow. */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[14rem] max-w-md">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
          <input
            ref={searchRef}
            type="text"
            role="searchbox"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={view === 'approvals' ? 'Search title, ID, requester, workflow, resource…' : 'Search title, ID, workflow, status, resource…'}
            aria-label={`Search ${noun}`}
            className="h-9 w-full rounded-md border border-gray-300 bg-white pl-8 pr-8 text-sm placeholder:text-gray-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
          />
          {query && (
            <button
              type="button"
              onClick={() => { setQuery(''); searchRef.current?.focus(); }}
              aria-label="Clear search"
              className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-gray-400 hover:text-gray-700"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>
        <div role="group" aria-label="Status" className="inline-flex rounded-md border border-gray-200 bg-gray-50 p-0.5">
          {statusOptions.map((o) => (
            <button
              key={o.value}
              type="button"
              aria-pressed={statusFilter === o.value}
              onClick={() => setStatusFilter(o.value)}
              className={cn(
                'rounded px-2.5 py-1 text-xs font-medium transition-colors whitespace-nowrap',
                statusFilter === o.value ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-800',
              )}
            >
              {o.label}
              <span className="ml-1 tabular-nums text-gray-400">{o.count}</span>
            </button>
          ))}
        </div>
        {workflowOptions.length > 1 && (
          <select
            ref={workflowSelectRef}
            aria-label="Workflow"
            value={workflowFilter?.value ?? ''}
            onChange={(e) => setWorkflowFilter(e.target.value || null)}
            className="h-9 max-w-[16rem] rounded-md border border-gray-300 bg-white px-2 text-sm text-gray-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
          >
            <option value="">All workflows</option>
            {workflowOptions.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label} ({o.count}{view === 'approvals' ? ' waiting' : ''})
              </option>
            ))}
          </select>
        )}
        {workflowFilter && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => { setWorkflowFilter(null); workflowSelectRef.current?.focus(); }}
            className="h-8 px-2 text-gray-600"
          >
            <X className="w-4 h-4 mr-1" />
            Clear filter
          </Button>
        )}
        <span className="ml-auto text-xs text-gray-500 tabular-nums" role="status">
          {loaded && totalForView > 0 && `${visible.length} of ${totalForView}`}
        </span>
      </div>

      {agingSummary && (
        <div className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-1.5 text-sm text-amber-800">
          <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
          <span>{agingSummary}</span>
        </div>
      )}

      {selectedApprovals.length > 0 && (
        <div className="flex flex-wrap items-center gap-3 rounded-md border border-green-200 bg-green-50 px-3 py-1.5 text-sm">
          <span className="font-medium text-green-900">{selectedApprovals.length} selected</span>
          {tooManySelected && (
            <span className="text-xs text-red-600" role="status">Select at most {BULK_APPROVE_MAX} at a time.</span>
          )}
          <div className="ml-auto flex gap-2">
            <Button size="sm" variant="ghost" className="h-7" onClick={() => setSelectedIds(new Set())}>Clear</Button>
            <Button
              size="sm"
              onClick={() => setBulkOpen(true)}
              disabled={tooManySelected}
              className="h-7 bg-green-600 hover:bg-green-700 text-white"
            >
              <ListChecks className="w-4 h-4 mr-1.5" />
              Approve selected ({selectedApprovals.length})
            </Button>
          </div>
        </div>
      )}

      <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white shadow-sm">
        {/* Fixed layout: the title column takes what's left and truncates, so
            the action buttons never get pushed off the edge. */}
        <table
          className={cn(
            'w-full table-fixed text-left',
            // Room for the title as columns appear; narrower than this, the box scrolls.
            view === 'approvals' ? 'min-w-[40rem] lg:min-w-[52rem] xl:min-w-[62rem]'
              : view === 'all' ? 'min-w-[32rem] sm:min-w-[36rem] lg:min-w-[52rem] xl:min-w-[62rem]'
                : 'min-w-[32rem] sm:min-w-[36rem] lg:min-w-[52rem]',
          )}
        >
          <thead>
            <tr className="border-b border-gray-200 bg-gray-50 text-xs font-semibold uppercase tracking-wide text-gray-500">
              {view === 'approvals' && (
                <th className="w-9 py-2 pl-3 pr-0">
                  {hasBulkCandidates && (
                    <input
                      type="checkbox"
                      className="w-4 h-4 align-middle"
                      aria-label="Select all you can approve"
                      title={bulkSelectable.length === 0 ? SELF_APPROVAL_MESSAGE : `Select all you can approve (${bulkSelectable.length})`}
                      checked={allSelected}
                      ref={(el) => {
                        if (el) el.indeterminate = someSelected;
                      }}
                      onChange={toggleSelectAll}
                      disabled={bulkSelectable.length === 0}
                    />
                  )}
                </th>
              )}
              <th className="py-2 px-3">Request</th>
              <th className="hidden lg:table-cell w-44 py-2 px-3">Workflow</th>
              {view !== 'mine' && <th className="hidden xl:table-cell w-40 py-2 px-3">Requester</th>}
              <th className={cn('py-2 px-3', view === 'approvals' ? 'w-32' : 'w-44')}>Status</th>
              {view === 'approvals' ? (
                <th className="hidden sm:table-cell w-20 py-2 px-3" title="How long it has waited, or when it was last updated">Age</th>
              ) : (
                <>
                  <th className="hidden lg:table-cell w-24 py-2 px-3">Submitted</th>
                  <th className="hidden sm:table-cell w-24 py-2 px-3">Updated</th>
                </>
              )}
              <th className={cn('py-2 px-3 text-right', view === 'approvals' ? 'w-48' : 'w-20')}>
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {!loaded ? (
              <tr>
                <td colSpan={colSpan} className="py-12 text-center text-sm text-gray-500">
                  <Loader2 className="mx-auto mb-2 h-5 w-5 animate-spin" />
                  Loading {noun}…
                </td>
              </tr>
            ) : emptyState ? (
              <tr>
                <td colSpan={colSpan} className="py-12 text-center">
                  <CheckCircle2 className="mx-auto mb-2 h-8 w-8 text-gray-300" />
                  <p className="font-medium text-gray-800">{emptyState.title}</p>
                  {emptyState.body && <p className="mt-0.5 text-sm text-gray-500">{emptyState.body}</p>}
                  <div className="mt-3 flex justify-center gap-2">
                    {query && <Button size="sm" variant="outline" onClick={() => setQuery('')}>Clear search</Button>}
                    {totalForView > 0 && statusFilter !== 'all' && (
                      <Button size="sm" variant="outline" onClick={() => setStatusFilter('all')}>Show all statuses</Button>
                    )}
                    {workflowFilter && (
                      <Button size="sm" variant="outline" onClick={() => setWorkflowFilter(null)}>Show all workflows</Button>
                    )}
                  </div>
                </td>
              </tr>
            ) : view === 'approvals' ? (
              (shown as ApprovalItem[]).map(({ key, approval }) => (
                <ApprovalRow
                  key={key}
                  approval={approval}
                  colSpan={colSpan}
                  expanded={expanded.has(key)}
                  selectable
                  selected={selectedIds.has(approval.id)}
                  onToggleExpand={() => toggleExpanded(key)}
                  onToggleSelect={() => toggleSelected(approval.id)}
                  onAction={(action) => setDialog({ approval, action })}
                  onViewDetails={() => void openApprovalDetails(approval)}
                  canEdit={isPlatformAdmin && !!approval.canEditParameters}
                  detailsError={detailsError?.key === key ? detailsError.message : null}
                />
              ))
            ) : (
              (shown as RequestItem[]).map(({ key, request, workflow }) => (
                <RequestRow
                  key={key}
                  request={request}
                  workflow={workflow}
                  showRequester={view === 'all'}
                  onOpen={() => navigate(`/requests/${request.id}${view === 'all' ? '?view=all' : ''}`)}
                  onDelete={() => void handleDelete(request.id)}
                  deleting={isDeleting === request.id}
                />
              ))
            )}
          </tbody>
        </table>
        {visible.length > shown.length && (
          <div className="border-t border-gray-100 px-3 py-2 text-center">
            <Button size="sm" variant="ghost" onClick={() => setPage({ key: filterKey, limit: limit + PAGE_SIZE })}>
              Show {Math.min(PAGE_SIZE, visible.length - shown.length)} more of {visible.length - shown.length}
            </Button>
          </div>
        )}
      </div>
      {view !== 'approvals' && (view === 'mine' ? myRequests : requests).length >= REQUEST_LIST_CAP && (
        <p className="text-xs text-gray-500">
          Showing the {REQUEST_LIST_CAP} most recent requests. Older ones still open from their links.
        </p>
      )}

      {dialog && (
        <ApprovalActionDialog approval={dialog.approval} action={dialog.action} onClose={() => setDialog(null)} />
      )}
      {bulkOpen && (
        <BulkApproveDialog
          selected={selectedApprovals}
          onClose={(sent) => {
            setBulkOpen(false);
            if (sent) setSelectedIds(new Set());
          }}
          onDone={async () => {
            await Promise.all([fetchApprovals(), fetchRequests()]);
          }}
        />
      )}
      {inspected && <RequestDetailsModal request={inspected} onClose={closeInspect} />}
      {!inspected && linked && <RequestDetailsModal request={linked} onClose={closeLinked} />}
    </div>
  );
}
