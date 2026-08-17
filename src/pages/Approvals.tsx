import { useState, useEffect, useCallback } from 'react';
import { useRequestStore } from '../stores/requestStore';
import { useUserStore } from '../stores/userStore';
import { RequestDetailsModal } from '../components/RequestDetailsModal';
import { RequestStateList } from './Requests';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Textarea } from '../components/ui/textarea';
import { CheckCircle2, ClipboardCheck, UserPlus, Clock, Check, X, Shield, Database, Badge, Calendar, Trash2, ArrowRight, Pencil, Settings2, AlertCircle } from 'lucide-react';
import { format } from 'date-fns';
import { formatInTimeZone } from 'date-fns-tz';
import { api } from '../services/api';
import type { Approval, ApprovalAction, Delegation } from '../types';

const isOverdue = (dueAt: string) => new Date(dueAt).getTime() < Date.now();

// Aging. A manual task can park a request indefinitely — there is no timeout and
// nothing chases it — so "how long has this been sitting here" has to be visible
// in the inbox rather than something you only learn when the requester asks.
const AGING_DAYS = 3;
const STALE_DAYS = 7;

const waitingDays = (createdAt: string) => {
  const started = new Date(createdAt).getTime();
  if (!Number.isFinite(started)) return 0;
  return Math.floor((Date.now() - started) / 86_400_000);
};


export function Approvals() {
  const approvals = useRequestStore((state) => state.approvals);
  const requests = useRequestStore((state) => state.requests);
  const delegations = useRequestStore((state) => state.delegations);
  const fetchApprovals = useRequestStore((state) => state.fetchApprovals);
  const fetchRequests = useRequestStore((state) => state.fetchRequests);
  const fetchDelegations = useRequestStore((state) => state.fetchDelegations);
  const addDelegation = useRequestStore((state) => state.addDelegation);
  const removeDelegation = useRequestStore((state) => state.removeDelegation);
  const processApproval = useRequestStore((state) => state.processApproval);

  const [selectedApproval, setSelectedApproval] = useState<Approval | null>(null);
  const [inspectedRequestId, setInspectedRequestId] = useState<string | null>(null);
  const inspectedRequest = requests.find(r => r.id === inspectedRequestId);
  const [actionType, setActionType] = useState<'approve' | 'reject' | 'delegate' | 'edit' | null>(null);
  const [note, setNote] = useState('');
  const [delegateEmail, setDelegateEmail] = useState('');
  const [isProcessing, setIsProcessing] = useState(false);
  // Edit-parameters state
  const [editParams, setEditParams] = useState<Record<string, string>>({});

  // New delegation state
  const [showDelegationForm, setShowDelegationForm] = useState(false);
  const [newDelegateEmail, setNewDelegateEmail] = useState('');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [delegationsToMe, setDelegationsToMe] = useState<Delegation[]>([]);

  const { currentUser } = useUserStore();
  const currentUserEmail = currentUser?.email || "admin@example.com";

  const fetchAllData = useCallback(async () => {
    await Promise.all([
      fetchApprovals(),
      fetchRequests(),
      fetchDelegations(currentUserEmail),
      (async () => {
        const toMe = await api.getDelegations(undefined, currentUserEmail);
        setDelegationsToMe(toMe);
      })()
    ]);
  }, [fetchApprovals, fetchRequests, fetchDelegations, currentUserEmail]);

  useEffect(() => {
    fetchAllData();
  }, [fetchAllData]);

  // User roles from actual user object, mapped to the internal format
  const userRoles = currentUser?.roles.map(r => r.toLowerCase().replace(' ', '_')) || [];
  
  const roleLabels: Record<string, string> = {
    platform_admin: 'Platform Admin',
    data_owner: 'Data Owner',
    manager: 'Manager',
    governance_admin: 'Governance Admin',
    security_admin: 'Security Admin',
    finance_admin: 'Finance Admin',
    // Not a role: a manual_task item is work assigned to you, not an approval
    // you grant. Labeling it "Manual Task" keeps that distinction on screen.
    manual_task: 'Manual Task'
  };

  // Filter approvals by user's roles
  // Note: The backend already filters the approvals list to only those the user is authorized to see.
  // We just need to separate them into pending and completed.
  // Oldest first: the thing that has been blocking a requester the longest is the
  // thing to look at, and a manual task with nothing chasing it can otherwise sit
  // at the bottom of the list forever.
  const pendingApprovals = approvals
    .filter((a) => a.status === 'pending')
    .sort((a, b) => new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime());

  // One line at the top of the inbox so aging registers before you start clicking.
  const agingSummary = (() => {
    const aging = pendingApprovals.filter((a) => waitingDays(a.createdAt) >= AGING_DAYS);
    if (aging.length === 0) return null;
    const overdue = pendingApprovals.filter((a) => a.dueAt && isOverdue(a.dueAt)).length;
    const manual = aging.filter((a) => a.approvalType === 'manual_task').length;
    const oldest = Math.max(...aging.map((a) => waitingDays(a.createdAt)));
    const parts = [
      `${aging.length} item${aging.length === 1 ? '' : 's'} have been waiting ${AGING_DAYS}+ days (oldest: ${oldest})`,
    ];
    if (manual > 0) {
      parts.push(
        `${manual} ${manual === 1 ? 'is a manual task' : 'are manual tasks'} — those requests stay paused until someone marks the work done`,
      );
    }
    if (overdue > 0) parts.push(`${overdue} past its due date`);
    return `${parts.join('; ')}.`;
  })();

  const completedApprovals = approvals
    .filter((a) => a.status !== 'pending')
    .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime());

  const handleAction = async (approval: Approval, action: 'approve' | 'reject' | 'delegate' | 'edit') => {
    setSelectedApproval(approval);
    setActionType(action);
    setNote('');
    setDelegateEmail('');
    if (action === 'edit' && approval.workflowParameters) {
      // Pre-populate editor with current parameters (as strings for form inputs)
      const stringified: Record<string, string> = {};
      Object.entries(approval.workflowParameters).forEach(([k, v]) => {
        stringified[k] = typeof v === 'object' ? JSON.stringify(v) : String(v ?? '');
      });
      setEditParams(stringified);
    } else {
      setEditParams({});
    }
  };

  const handleAddDelegation = async () => {
    if (!newDelegateEmail || !startDate || !endDate) {
      alert('Please fill in all fields');
      return;
    }

    try {
      await addDelegation({
        delegatee_email: newDelegateEmail,
        start_date: new Date(startDate).toISOString(),
        end_date: new Date(endDate).toISOString(),
      });
      setShowDelegationForm(false);
      setNewDelegateEmail('');
      setStartDate('');
      setEndDate('');
    } catch {
      alert('Failed to add delegation');
    }
  };

  const submitAction = async () => {
    if (!selectedApproval || !actionType) return;

    if (actionType === 'reject' && !note.trim()) {
      alert('Please provide a rejection note');
      return;
    }

    if (actionType === 'delegate' && !delegateEmail.trim()) {
      alert('Please provide an email address to delegate to');
      return;
    }

    setIsProcessing(true);

    const action: ApprovalAction = {
      approvalId: selectedApproval.id,
      action: actionType,
      note: (actionType === 'reject' || actionType === 'edit') ? note : undefined,
      delegatedToEmail: actionType === 'delegate' ? delegateEmail : undefined,
      newParameters: actionType === 'edit' ? editParams : undefined,
    };

    try {
      if (actionType === 'edit') {
        // Coerce string values back to their original types where possible
        const coerced: Record<string, unknown> = {};
        Object.entries(editParams).forEach(([k, v]) => {
          try { coerced[k] = JSON.parse(v); } catch { coerced[k] = v; }
        });
        await api.editRequestParameters(selectedApproval.requestId, coerced, note || undefined);
      } else {
        await processApproval(action);
      }
    } catch (error) {
      console.error('Error processing approval:', error);
    } finally {
      setIsProcessing(false);
      setSelectedApproval(null);
      setActionType(null);
      setNote('');
      setDelegateEmail('');
    }
  };

  const approvalCapabilities = {
    platform_admin: [
      'Workspace provisioning requests',
      'Service principal creation',
      'Workspace access requests',
      'System configuration changes',
    ],
    data_owner: [
      'Data access requests for platform_catalog',
      'Catalog/schema/table creation in platform_catalog',
      'Data sharing requests for platform_catalog',
      'Data certification requests (Data SME Review)',
    ],
    manager: [
      'Workspace provisioning requests (Budget)',
      'Training requirements'
    ],
    governance_admin: [
      'Data certification requests (Governance Admin Review)',
      'Policy exceptions',
    ]
  };

  return (
    <div className="space-y-6 pb-20">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-900 mb-2">Approvals & Delegations</h1>
          <p className="text-gray-600">Review requests or manage your Out-of-Office delegations</p>
        </div>
        <Button
          onClick={() => setShowDelegationForm(!showDelegationForm)}
          variant={showDelegationForm ? "outline" : "default"}
          className="flex items-center gap-2"
        >
          <Calendar className="w-4 h-4" />
          {showDelegationForm ? 'Cancel Delegation' : 'Manage OOO / Delegation'}
        </Button>
      </div>

      {showDelegationForm && (
        <Card className="border-2 border-primary/20 shadow-lg animate-in fade-in slide-in-from-top-4 duration-300">
          <CardHeader className="bg-primary/5">
            <CardTitle className="text-lg flex items-center gap-2">
              <UserPlus className="w-5 h-5 text-primary" />
              New "Delegate All" Configuration
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-6 space-y-6">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
              <div className="space-y-2">
                <label className="text-sm font-semibold text-gray-700">Delegate To</label>
                <Input
                  placeholder="delegate@example.com"
                  value={newDelegateEmail}
                  onChange={(e) => setNewDelegateEmail(e.target.value)}
                />
                <p className="text-[10px] text-gray-500 italic">This person will receive all your approval tasks during the specified period.</p>
              </div>
              <div className="space-y-2">
                <label className="text-sm font-semibold text-gray-700">Start Date</label>
                <Input
                  type="date"
                  value={startDate}
                  onChange={(e) => setStartDate(e.target.value)}
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-semibold text-gray-700">End Date</label>
                <Input
                  type="date"
                  value={endDate}
                  onChange={(e) => setEndDate(e.target.value)}
                />
              </div>
            </div>

            <div className="flex justify-end gap-3 pt-4 border-t border-gray-100">
              <Button variant="outline" onClick={() => setShowDelegationForm(false)}>Cancel</Button>
              <Button onClick={handleAddDelegation} className="bg-primary text-white">
                Enable Global Delegation
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {delegations.length > 0 && (
        <Card className="border-blue-100 bg-blue-50/30">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-bold text-blue-900 uppercase tracking-wider flex items-center gap-2">
              <Calendar className="w-4 h-4" />
              Your Active OOO Delegations
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {delegations.map((del) => (
                <div key={del.id} className="bg-white border border-blue-200 rounded-lg p-4 flex items-center justify-between shadow-sm">
                  <div className="flex items-center gap-6">
                    <div className="flex items-center gap-3">
                      <div className="w-8 h-10 rounded-full bg-blue-100 flex items-center justify-center text-blue-700 font-bold text-xs">
                        {currentUserEmail[0].toUpperCase()}
                      </div>
                      <ArrowRight className="w-4 h-4 text-gray-400" />
                      <div className="w-8 h-10 rounded-full bg-green-100 flex items-center justify-center text-green-700 font-bold text-xs">
                        {del.delegatee_email[0].toUpperCase()}
                      </div>
                    </div>
                    <div>
                      <p className="text-sm font-semibold text-gray-900">
                        All approvals delegated to <span className="text-blue-600">{del.delegatee_email}</span>
                      </p>
                      <p className="text-xs text-gray-500">
                        Period: {format(new Date(del.start_date), 'MMM d, yyyy')} — {format(new Date(del.end_date), 'MMM d, yyyy')}
                      </p>
                    </div>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-red-600 hover:text-red-700 hover:bg-red-50"
                    onClick={() => removeDelegation(del.id)}
                  >
                    <Trash2 className="w-4 h-4 mr-2" />
                    Revoke
                  </Button>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {delegationsToMe.length > 0 && (
        <Card className="border-green-100 bg-green-50/30">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-bold text-green-900 uppercase tracking-wider flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4" />
              Incoming Delegations (You are Acting Admin)
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {delegationsToMe.map((del) => (
                <div key={del.id} className="bg-white border border-green-200 rounded-lg p-4 flex items-center shadow-sm">
                  <div className="flex items-center gap-6">
                    <div className="flex items-center gap-3">
                      <div className="w-8 h-10 rounded-full bg-blue-100 flex items-center justify-center text-blue-700 font-bold text-xs">
                        {del.delegator_email[0].toUpperCase()}
                      </div>
                      <ArrowRight className="w-4 h-4 text-gray-400" />
                      <div className="w-8 h-10 rounded-full bg-green-100 flex items-center justify-center text-green-700 font-bold text-xs">
                        {currentUserEmail[0].toUpperCase()}
                      </div>
                    </div>
                    <div>
                      <p className="text-sm font-semibold text-gray-900">
                        You are acting as <span className="text-green-600">{del.delegator_email}</span>
                      </p>
                      <p className="text-xs text-gray-500">
                        Delegation active until {format(new Date(del.end_date), 'MMM d, yyyy')}
                      </p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Badge className="w-5 h-5 text-primary" />
            My Roles & Approval Capabilities
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {userRoles.length > 0 ? userRoles.map((role) => (
              <div key={role} className="border border-gray-200 rounded-lg p-4">
                <div className="flex items-center gap-2 mb-3">
                  {role === 'platform_admin' ? (
                    <Shield className="w-5 h-5 text-primary" />
                  ) : role === 'data_owner' ? (
                    <Database className="w-5 h-5 text-primary" />
                  ) : (
                    <UserPlus className="w-5 h-5 text-primary" />
                  )}
                  <h3 className="font-semibold text-gray-900">{roleLabels[role] || role.replace('_', ' ')}</h3>
                </div>
                {role === 'data_owner' && (
                  <p className="text-sm text-gray-600 mb-2">Data Owner for: <strong>platform_catalog</strong></p>
                )}
                <ul className="space-y-1.5 text-sm text-gray-600">
                  {approvalCapabilities[role as keyof typeof approvalCapabilities]?.map((capability, idx) => (
                    <li key={idx} className="flex items-start gap-2">
                      <span className="text-primary shrink-0 mt-1.5 text-xs">•</span>
                      <span className="leading-relaxed">{capability}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )) : (
              <p className="text-sm text-gray-500 italic">No approval roles assigned.</p>
            )}
          </div>
        </CardContent>
      </Card>

      {pendingApprovals.length === 0 && completedApprovals.length === 0 && (
        <Card>
          <CardContent className="py-12 text-center">
            <CheckCircle2 className="w-12 h-12 text-gray-400 mx-auto mb-4" />
            <p className="text-lg text-gray-600 mb-2">No approvals found</p>
            <p className="text-sm text-gray-500">You're all caught up!</p>
          </CardContent>
        </Card>
      )}

      {pendingApprovals.length > 0 && (
        <div className="space-y-4">
          <h2 className="text-xl font-semibold text-gray-900">Pending ({pendingApprovals.length})</h2>
          {agingSummary && (
            <div className="bg-amber-50 border border-amber-200 text-amber-800 rounded-md px-4 py-2 text-sm flex items-start gap-2">
              <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
              <span>{agingSummary}</span>
            </div>
          )}
          {pendingApprovals.map((approval) => (
            <Card key={approval.id} className="border-l-4 border-l-primary">
              <CardHeader>
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <CardTitle className="text-lg flex items-center gap-2 flex-wrap">
                      {approval.requestTitle}
                      {waitingDays(approval.createdAt) >= AGING_DAYS && (
                        <span
                          className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${
                            waitingDays(approval.createdAt) >= STALE_DAYS
                              ? 'bg-red-100 text-red-800'
                              : 'bg-amber-100 text-amber-800'
                          }`}
                          title={
                            approval.approvalType === 'manual_task'
                              ? "The request stays paused until someone marks this manual work done — it won't time out on its own."
                              : 'The request stays paused until someone decides.'
                          }
                        >
                          <AlertCircle className="w-3.5 h-3.5" />
                          waiting {waitingDays(approval.createdAt)} days
                        </span>
                      )}
                    </CardTitle>
                    <div className="mt-2 space-y-1 text-sm text-gray-600">
                      <p>
                        <span className="font-medium">Requested by:</span> {approval.requestedBy} ({approval.requestedByEmail})
                      </p>
                      <p>
                        <span className="font-medium">Type:</span> {approval.requestType.replace(/_/g, ' ')}
                      </p>
                      <p>
                        <span className="font-medium">
                          {approval.approvalType === 'manual_task' ? 'Task Type:' : 'Approval Type:'}
                        </span>{' '}
                        <span className={`px-2 py-1 rounded-full text-xs font-medium ${approval.approvalType === 'platform_admin'
                          ? 'bg-blue-100 text-blue-800'
                          : approval.approvalType === 'data_owner'
                            ? 'bg-purple-100 text-purple-800'
                            : approval.approvalType === 'manual_task'
                              ? 'bg-amber-100 text-amber-800'
                              : 'bg-gray-100 text-gray-800'
                          }`}>
                          {roleLabels[approval.approvalType] || approval.approvalType.replace(/_/g, ' ')}
                        </span>
                      </p>
                      {approval.dueAt && (
                        <p className="flex items-center gap-1">
                          <Clock className="w-4 h-4" />
                          <span className={isOverdue(approval.dueAt) ? 'text-red-600 font-medium' : ''}>
                            {isOverdue(approval.dueAt) ? 'Overdue — was due ' : 'Due '}
                            {formatInTimeZone(new Date(approval.dueAt), 'America/Los_Angeles', 'PPp zzz')}
                          </span>
                        </p>
                      )}
                      <p className="flex items-center gap-1">
                        <Clock className="w-4 h-4" />
                        <span>Requested {formatInTimeZone(new Date(approval.createdAt), 'America/Los_Angeles', 'PPp zzz')}</span>
                      </p>
                    </div>
                    {/* What the assignee actually has to go do. Without this the
                        row is an unexplained "something is waiting on you". */}
                    {approval.approvalType === 'manual_task' && (
                      <div className="mt-3 p-3 bg-amber-50 border border-amber-200 rounded-lg">
                        <p className="text-xs font-semibold text-amber-700 uppercase tracking-wide mb-2 flex items-center gap-1">
                          <ClipboardCheck className="w-3.5 h-3.5" />
                          What to do
                        </p>
                        <p className="text-sm text-gray-800 whitespace-pre-wrap">
                          {approval.instructions?.trim() ||
                            'No instructions were recorded on this task — ask a platform admin before marking it done.'}
                        </p>
                      </div>
                    )}
                    {/* Workflow parameters read-only view */}
                    {approval.workflowParameters && Object.keys(approval.workflowParameters).length > 0 && (
                      <div className="mt-3 p-3 bg-gray-50 border border-gray-200 rounded-lg">
                        <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2 flex items-center gap-1">
                          <Settings2 className="w-3.5 h-3.5" />
                          Workflow Parameters
                        </p>
                        <dl className="grid grid-cols-2 gap-x-4 gap-y-1">
                          {Object.entries(approval.workflowParameters).map(([k, v]) => (
                            <div key={k} className="contents">
                              <dt className="text-xs text-gray-500 font-medium truncate">{k}</dt>
                              <dd className="text-xs text-gray-800 font-mono break-all">
                                {typeof v === 'object' ? JSON.stringify(v) : String(v ?? '')}
                              </dd>
                            </div>
                          ))}
                        </dl>
                      </div>
                    )}
                  </div>
                </div>
              </CardHeader>
              <CardContent>
                <div className="flex flex-wrap gap-2">
                  {/* A manual task is completed, not authorized — the same
                      approve/reject endpoints record the fact the poller waits
                      on, so only the wording changes. */}
                  <Button
                    onClick={() => handleAction(approval, 'approve')}
                    className="bg-green-600 hover:bg-green-700 text-white"
                  >
                    <Check className="w-4 h-4 mr-2" />
                    {approval.approvalType === 'manual_task' ? 'Mark done' : 'Approve'}
                  </Button>
                  <Button
                    onClick={() => handleAction(approval, 'reject')}
                    variant="outline"
                    className="border-red-300 text-red-700 hover:bg-red-50"
                  >
                    <X className="w-4 h-4 mr-2" />
                    {approval.approvalType === 'manual_task' ? "Can't complete" : 'Reject'}
                  </Button>
                  <Button
                    onClick={() => handleAction(approval, 'delegate')}
                    variant="outline"
                    className="border-blue-300 text-blue-700 hover:bg-blue-50"
                  >
                    <UserPlus className="w-4 h-4 mr-2" />
                    Delegate
                  </Button>

                  {/* Edit & Restart — platform_admin only */}
                  {userRoles.includes('platform_admin') && approval.approvalType === 'platform_admin' && (
                    <Button
                      onClick={() => handleAction(approval, 'edit')}
                      variant="outline"
                      className="border-orange-300 text-orange-700 hover:bg-orange-50"
                    >
                      <Pencil className="w-4 h-4 mr-2" />
                      Edit &amp; Restart
                    </Button>
                  )}

                  <Button
                    onClick={() => {
                      setInspectedRequestId(approval.requestId);
                      if (requests.length === 0) {
                        fetchRequests();
                      }
                    }}
                    variant="outline"
                    className="border-gray-300 text-gray-700 hover:bg-gray-50"
                  >
                    <ArrowRight className="w-4 h-4 mr-2" />
                    View Details
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {inspectedRequest && (
        <RequestDetailsModal
          request={inspectedRequest}
          onClose={() => setInspectedRequestId(null)}
          RequestStateList={RequestStateList}
        />
      )}



      {completedApprovals.length > 0 && (
        <div className="space-y-4 mt-8">
          <h2 className="text-xl font-semibold text-gray-900">Completed ({completedApprovals.length})</h2>
          {completedApprovals.map((approval) => (
            <Card key={approval.id} className="border-l-4 border-l-gray-300">
              <CardHeader>
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <CardTitle className="text-lg">{approval.requestTitle}</CardTitle>
                    <div className="mt-2 space-y-1 text-sm text-gray-600">
                      <p>
                        <span className="font-medium">Requested by:</span> {approval.requestedBy}
                      </p>
                      <p className="flex items-center gap-2">
                        <span className={`px-2 py-1 rounded-full text-xs font-medium ${approval.status === 'approved' ? 'bg-green-100 text-green-800' :
                            approval.status === 'rejected' ? 'bg-red-100 text-red-800' :
                              approval.status === 'superseded' ? 'bg-orange-100 text-orange-800' :
                                'bg-blue-100 text-blue-800'
                          }`}>
                          {approval.status === 'approved' ? 'Approved' :
                            approval.status === 'rejected' ? 'Rejected' :
                              approval.status === 'superseded' ? 'Superseded by Edit' :
                                'Delegated'}
                        </span>
                        {approval.delegatedTo && (
                          <span className="text-gray-500">→ {approval.delegatedTo}</span>
                        )}
                      </p>
                      {approval.rejectionNote && (
                        <p className="mt-2 p-2 bg-red-50 border border-red-200 rounded text-red-800">
                          <span className="font-medium">Rejection Note:</span> {approval.rejectionNote}
                        </p>
                      )}
                      {approval.supersededNote && (
                        <p className="mt-2 p-2 bg-orange-50 border border-orange-200 rounded text-orange-800 flex items-start gap-2">
                          <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
                          <span>{approval.supersededNote}</span>
                        </p>
                      )}
                      <p className="text-xs text-gray-500">
                        {formatInTimeZone(new Date(approval.updatedAt), 'America/Los_Angeles', 'PPp zzz')}
                      </p>
                    </div>
                  </div>
                </div>
              </CardHeader>
              <CardContent>
                <div className="flex gap-2">

                  <Button
                    onClick={() => {
                      setInspectedRequestId(approval.requestId);
                      if (requests.length === 0) {
                        fetchRequests();
                      }
                    }}
                    variant="outline"
                    className="border-gray-300 text-gray-700 hover:bg-gray-50"
                  >
                    <ArrowRight className="w-4 h-4 mr-2" />
                    View Details
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {selectedApproval && actionType && (() => {
        const isManualTask = selectedApproval.approvalType === 'manual_task';
        return (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <Card className="w-full max-w-md mx-4">
            <CardHeader>
              <CardTitle>
                {actionType === 'approve' &&
                  (isManualTask ? 'Mark Task Done' : 'Approve Request')}
                {actionType === 'reject' &&
                  (isManualTask ? "Can't Complete Task" : 'Reject Request')}
                {actionType === 'delegate' && 'Delegate Approval'}
                {actionType === 'edit' && (
                  <span className="flex items-center gap-2">
                    <Pencil className="w-4 h-4 text-orange-500" />
                    Edit Parameters &amp; Restart
                  </span>
                )}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="text-sm text-gray-600">
                <span className="font-medium">Request:</span> {selectedApproval.requestTitle}
              </p>

              {actionType === 'edit' && (
                <div className="space-y-3">
                  <p className="text-xs text-gray-500 flex items-start gap-1.5 bg-orange-50 border border-orange-200 rounded p-2">
                    <AlertCircle className="w-4 h-4 text-orange-500 shrink-0 mt-0.5" />
                    Editing parameters will supersede the current approval and restart the Terraform planning phase with the new values.
                  </p>
                  <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Parameters</p>
                  <div className="space-y-2 max-h-64 overflow-y-auto pr-1">
                    {Object.keys(editParams).length === 0 ? (
                      <p className="text-sm text-gray-400 italic">No editable parameters available.</p>
                    ) : (
                      Object.entries(editParams).map(([key, value]) => (
                        <div key={key} className="flex items-center gap-2">
                          <label className="text-xs font-mono text-gray-600 w-40 shrink-0 truncate" title={key}>{key}</label>
                          <input
                            type="text"
                            value={value}
                            onChange={(e) => setEditParams(prev => ({ ...prev, [key]: e.target.value }))}
                            className="flex-1 text-xs font-mono border border-gray-300 rounded px-2 py-1 focus:outline-none focus:ring-2 focus:ring-orange-400"
                          />
                        </div>
                      ))
                    )}
                  </div>
                </div>
              )}

              {actionType === 'approve' && isManualTask && selectedApproval.instructions?.trim() && (
                <div className="text-sm text-gray-700 bg-amber-50 border border-amber-200 rounded p-3">
                  <p className="text-xs font-semibold text-amber-700 uppercase tracking-wide mb-1">
                    Confirm you completed
                  </p>
                  <p className="whitespace-pre-wrap">{selectedApproval.instructions}</p>
                </div>
              )}

              {actionType === 'reject' && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">
                    {isManualTask ? 'Why it can\u2019t be completed' : 'Rejection Note'}{' '}
                    <span className="text-red-500">*</span>
                  </label>
                  <Textarea
                    placeholder={
                      isManualTask
                        ? 'Explain what blocked the task so the requester knows what happened...'
                        : 'Please provide a reason for rejection...'
                    }
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    className="w-full min-h-[100px]"
                  />
                </div>
              )}

              {actionType === 'edit' && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">
                    Note <span className="text-gray-400 font-normal">(optional)</span>
                  </label>
                  <Textarea
                    placeholder="Reason for editing parameters..."
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    className="w-full min-h-[80px]"
                  />
                </div>
              )}

              {actionType === 'delegate' && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">
                    Delegate To (Email) <span className="text-red-500">*</span>
                  </label>
                  <Input
                    type="email"
                    placeholder="user@example.com"
                    value={delegateEmail}
                    onChange={(e) => setDelegateEmail(e.target.value)}
                    className="w-full"
                  />
                  <p className="text-xs text-gray-500 mt-1">
                    This will temporarily transfer approval responsibility to the specified user.
                  </p>
                </div>
              )}

              <div className="flex gap-2 pt-4">
                <Button
                  onClick={submitAction}
                  disabled={
                    isProcessing ||
                    (actionType === 'reject' && !note.trim()) ||
                    (actionType === 'delegate' && !delegateEmail.trim()) ||
                    (actionType === 'edit' && Object.keys(editParams).length === 0)
                  }
                  className={actionType === 'approve' ? 'bg-green-600 hover:bg-green-700' :
                    actionType === 'reject' ? 'bg-red-600 hover:bg-red-700' :
                      actionType === 'edit' ? 'bg-orange-500 hover:bg-orange-600' :
                        'bg-blue-600 hover:bg-blue-700'}
                >
                  {isProcessing ? 'Processing...' :
                    actionType === 'approve' ? (isManualTask ? 'Confirm Done' : 'Confirm Approval') :
                      actionType === 'reject' ? (isManualTask ? "Confirm Can't Complete" : 'Confirm Rejection') :
                        actionType === 'edit' ? 'Apply & Restart' :
                          'Confirm Delegation'}
                </Button>
                <Button
                  variant="outline"
                  onClick={() => {
                    setSelectedApproval(null);
                    setActionType(null);
                    setNote('');
                    setDelegateEmail('');
                    setEditParams({});
                  }}
                  disabled={isProcessing}
                >
                  Cancel
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
        );
      })()}
    </div>
  );
}
