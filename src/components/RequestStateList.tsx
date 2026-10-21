import { useState, useEffect } from 'react';
import { formatInTimeZone } from 'date-fns-tz';
import { formatDistanceToNow, differenceInHours } from 'date-fns';
import { X, CheckCircle2, Circle, Loader2, AlertCircle } from 'lucide-react';
import { useRequestStore } from '../stores/requestStore';
import { useVisibleInterval } from '../hooks/useVisibleInterval';
import { Button } from './ui/button';
import { formatDate, parseUtcDate } from '../lib/requestDates';
import type { Request } from '../types';

const formatDuration = (startDate: string) => {
  if (!startDate) return '';
  const start = parseUtcDate(startDate);
  const now = new Date();
  const hours = differenceInHours(now, start);

  if (hours < 1) {
    return formatDistanceToNow(start, { addSuffix: true });
  }
  return `${hours} hours ago`;
};

function TrainingAction({ requestId }: { requestId: string }) {
  const completeTraining = useRequestStore((state) => state.completeTraining);
  const fetchRequests = useRequestStore((state) => state.fetchRequests);
  const [isLoading, setIsLoading] = useState(false);

  const handleCompleteTraining = async () => {
    setIsLoading(true);
    try {
      await completeTraining(requestId);
      await fetchRequests();
    } catch (error) {
      console.error('Failed to complete training:', error);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <Button
      size="sm"
      onClick={handleCompleteTraining}
      disabled={isLoading}
      className="bg-yellow-600 hover:bg-yellow-700 text-white h-8 text-xs"
    >
      {isLoading ? 'Updating...' : 'Mark Complete (Debug)'}
    </Button>
  );
}

function ProvisioningProgress({ progress }: { progress: NonNullable<Request['stateMachine']['currentProgress']> }) {
  return (
    <div className="mt-4 p-4 bg-blue-50 border border-blue-200 rounded-lg">
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm font-medium text-blue-900">{progress.message}</span>
        <span className="text-sm font-bold text-blue-900">{progress.percent}%</span>
      </div>
      <div className="w-full bg-blue-200 rounded-full h-2.5">
        <div
          className="bg-blue-600 h-2.5 rounded-full transition-all duration-500 ease-out"
          style={{ width: `${progress.percent}%` }}
        ></div>
      </div>
      <p className="text-[10px] text-blue-700 mt-2 text-right italic">
        Last updated: {formatDate(progress.timestamp)}
      </p>
    </div>
  );
}

export function RequestStateList({ request }: { request: Request }) {
  const fetchApprovals = useRequestStore((state) => state.fetchApprovals);
  const fetchRequests = useRequestStore((state) => state.fetchRequests);

  useEffect(() => {
    fetchApprovals();
  }, [fetchApprovals, request.status]);

  // Poll for request updates every 5 seconds while viewing an active request,
  // so state transitions show up live. Paused while the tab is hidden.
  const isActive = !['completed', 'failed', 'rejected'].includes(request.status);
  useVisibleInterval(fetchRequests, 5000, isActive);

  // Collect all states to display
  let steps: { id: string; name: string; status: string; type?: string; typeLabel?: string | null; order: number; completedAt?: string; facts?: { type: string; timestamp: string; data: Record<string, string> }[] }[] = [];


  // Use the new linear states structure
  if (request.stateMachine.states && request.stateMachine.states.length > 0) {
    const stateSteps = request.stateMachine.states
      .filter(state => {
        // Skip terminal failure/rejection states if they aren't the current state
        // This ensures the "happy path" is the default view
        if (state.id === 'failed' && request.status !== 'failed') return false;
        if (state.id === 'rejected' && request.status !== 'rejected') return false;

        // Skip terminal success state if we want a cleaner list
        if (state.id === 'completed') return false;
        return true;
      })
      .map((state, index) => {
        let status: 'pending' | 'active' | 'completed' | 'rejected' | 'failed' = 'pending';
        if (state.isCompleted) {
          if (state.id === 'rejected' || state.name.toLowerCase().includes('rejected')) {
            status = 'rejected';
          } else {
            status = 'completed';
          }
        } else if (state.isActive) {
          if (request.status === 'completed') {
            status = 'completed';
          } else if (request.status === 'failed') {
            status = 'failed';
          } else {
            status = 'active';
          }
        }

        return {
          id: state.id,
          name: state.name,
          status,
          type: state.gateType,
          typeLabel: state.gateLabel,
          order: index + 1,
          completedAt: state.completedAt,
          facts: state.facts
        };
      });

    // Handle terminal "Completed" state - always show it as the final goal if not failed/rejected
    if (request.status !== 'failed' && request.status !== 'rejected') {
      const completionState = request.stateMachine.states.find(s => s.id === 'completed');
      stateSteps.push({
        id: 'completed_goal',
        name: 'Completed',
        status: request.status === 'completed' ? 'completed' : 'pending',
        type: undefined,
        typeLabel: undefined,
        order: stateSteps.length + 1,
        completedAt: completionState?.completedAt,
        facts: completionState?.facts ?? []
      });
    }

    // Fallback: If request is failed but we have no states (e.g. failed to load state machine),
    // add a synthetic failed step so the error is visible.
    if (request.status === 'failed') {
      const hasFailedStep = stateSteps.some(s => s.status === 'failed');
      if (!hasFailedStep) {
        // Replace the last active/pending step with failed, or append if needed
        const activeIdx = stateSteps.findIndex(s => s.status === 'active' || s.status === 'pending');
        if (activeIdx !== -1) {
          stateSteps[activeIdx].status = 'failed';
          stateSteps[activeIdx].name = `${stateSteps[activeIdx].name} (Failed)`;
        } else {
          stateSteps.push({
            id: 'system_failure',
            name: 'System Processing',
            status: 'failed',
            type: undefined,
            typeLabel: undefined,
            order: stateSteps.length + 1,
            completedAt: request.updatedAt,
            facts: []
          });
        }
      }
    }

    steps = [...steps, ...stateSteps];
  }

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-lg border border-gray-200 shadow-sm divide-y divide-gray-200">
        {steps.map((step, idx) => {
          const isCompleted = step.status === 'completed';
          const isRejected = step.status === 'rejected';
          const isFailed = step.status === 'failed';
          const isActive = step.status === 'active';
          const isTraining = step.id === 'training_pending';

          // Human approval gates (data_owner / manager / admin roles). The step
          // id is the stage name (e.g. "await_approval"), so we identify the
          // gate by its `type` rather than a hardcoded id.
          const HUMAN_GATE_TYPES = ['data_owner', 'manager', 'platform_admin', 'security', 'security_admin', 'governance_admin', 'finance_admin'];
          // A manual task pauses for a person but grants nothing, so it gets its
          // own "assigned to" block rather than the approver list.
          const isManualTask = step.type === 'manual_task';
          const isApprovalStep = !isManualTask && ((!!step.type && HUMAN_GATE_TYPES.includes(step.type)) || step.id === 'data_owner_approval' || /approval/i.test(step.id));
          const manualTaskApprovals = isManualTask
            ? (request.approvals || []).filter((a) => a.approvalType === 'manual_task')
            : [];
          const stepApprovals = isApprovalStep
            ? (request.approvals || []).filter((a) =>
                step.type ? a.approvalType === step.type : HUMAN_GATE_TYPES.includes(a.approvalType)
              )
            : [];

          const getStepBg = () => {
            if (isFailed || isRejected) return 'color-mix(in srgb, var(--brand-alert), transparent 95%)';
            if (isActive) return 'color-mix(in srgb, var(--brand-info), transparent 95%)';
            if (isCompleted) return 'color-mix(in srgb, var(--brand-success), transparent 95%)';
            return 'white';
          };

          return (
            <div
              key={`${step.id}-${idx}`}
              style={{ backgroundColor: getStepBg() }}
              className={`p-4 flex flex-col transition-colors border-l-4 ${isFailed || isRejected ? 'border-l-alert' :
                isActive ? 'border-l-info' :
                  isCompleted ? 'border-l-success' :
                    'border-l-gray-200'
                } first:rounded-t-lg last:rounded-b-lg hover:bg-opacity-80`}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-4 flex-1">
                  <div className={`w-10 h-10 rounded-full flex items-center justify-center shrink-0 ${isFailed ? 'bg-alert text-white' :
                    isRejected ? 'bg-alert text-white' :
                      isCompleted ? 'bg-success text-white' :
                        isActive ? 'bg-info text-white' :
                          'bg-gray-300 text-gray-600'
                    }`}>
                    {isFailed ? <AlertCircle className="w-6 h-6" /> :
                      isCompleted ? <CheckCircle2 className="w-6 h-6" /> :
                        isRejected ? <X className="w-6 h-6" /> :
                          isActive ? <Loader2 className="w-6 h-6 animate-spin" /> :
                            <Circle className="w-6 h-6" />}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <p className="text-base font-semibold text-gray-900">{step.name}</p>
                      {step.type && (
                        <span className={`text-xs text-gray-600 bg-gray-100 px-2 py-0.5 rounded-full ${step.typeLabel ? '' : 'capitalize'}`}>
                          {step.typeLabel || step.type.replace(/_/g, ' ')}
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-3 text-sm text-gray-600">
                      <span className={`font-medium ${isFailed ? 'text-alert font-bold' :
                        isRejected ? 'text-alert' :
                          isCompleted ? 'text-success' :
                            isActive ? 'text-info' :
                              'text-gray-500'
                        }`}>
                        {isFailed ? 'Failed' :
                          isCompleted ? 'Completed' :
                            isRejected ? 'Rejected' :
                              isActive ? 'In Progress' :
                                'Pending'}
                      </span>
                      {(step.completedAt || step.id === request.stateMachine.states[0]?.id) && (
                        <>
                          <span className="text-gray-400">•</span>
                          <span className="text-gray-500">
                            {formatDate(step.completedAt || request.createdAt)} ({formatDuration(step.completedAt || request.createdAt)})
                          </span>
                        </>
                      )}
                    </div>
                  </div>
                </div>

                {isTraining && isActive && !isCompleted && !isRejected && (
                  <div className="flex items-center gap-3 ml-4">
                    <div className="text-sm text-yellow-800 bg-yellow-100 px-3 py-2 rounded-lg border border-yellow-300 flex items-center gap-2">
                      <AlertCircle className="w-4 h-4" />
                      Action Required
                    </div>
                    <TrainingAction requestId={request.id} />
                  </div>
                )}
              </div>

              {/* Progress Bar for Provisioning */}
              {isActive && step.id === 'provisioning' && request.stateMachine.currentProgress && (
                <div className="ml-14 mt-3">
                  <ProvisioningProgress progress={request.stateMachine.currentProgress} />
                </div>
              )}

              {/* Approver(s) for this human gate (data_owner / manager / admin).
                  Surfaced so the requester can see who must approve — and who
                  approved/rejected. Falls back to a clear message when no
                  specific owner was resolved (then any Platform Admin can act). */}
              {isApprovalStep && (
                <div className="ml-14 mt-3 space-y-2">
                  <p className="text-[10px] uppercase tracking-wider text-gray-400 font-bold mb-1">
                    {stepApprovals.length > 1 ? 'Approvers' : 'Approver'}
                  </p>
                  {stepApprovals.length > 0 ? (
                    <div className="grid gap-2">
                      {stepApprovals.map((approval, aIdx) => (
                        <div key={aIdx} className="flex items-center justify-between bg-white border border-gray-200 rounded p-2 text-sm">
                          <div className="flex items-center gap-2">
                            {approval.status === 'approved' ? (
                              <CheckCircle2 className="w-4 h-4 text-success" />
                            ) : approval.status === 'rejected' ? (
                              <X className="w-4 h-4 text-alert" />
                            ) : (
                              <Circle className="w-4 h-4 text-gray-300" />
                            )}
                            <span className="font-medium text-gray-700">
                              {approval.assignedToEmail || approval.approverLabel || approval.assignedToRole || 'Any Platform Admin'}
                            </span>
                          </div>
                          <span className={`text-xs font-medium ${
                            approval.status === 'approved' ? 'text-success' :
                            approval.status === 'rejected' ? 'text-alert' :
                            'text-gray-500'
                          }`}>
                            {approval.status === 'approved' ? `Approved by ${approval.approvedBy || approval.assignedToEmail || approval.assignedToRole || 'Platform Admin'}` :
                             approval.status === 'rejected' ? `Rejected by ${approval.rejectedBy || approval.assignedToEmail || approval.assignedToRole || 'Platform Admin'}` :
                             'Pending'}
                          </span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="bg-white border border-gray-200 rounded p-2 text-sm italic text-gray-500">
                      {step.type === 'manager'
                        ? 'No manager was resolved for this request — pending review by a Platform Admin.'
                        : step.type === 'data_owner'
                        ? 'No data owner was resolved for the requested asset — pending review by a Platform Admin.'
                        : 'No approver was resolved — pending review by a Platform Admin.'}
                    </div>
                  )}
                </div>
              )}

              {/* Manual task: who is doing the off-platform work, and what they
                  were asked to do. Nothing here is an approval. */}
              {isManualTask && (
                <div className="ml-14 mt-3 space-y-2">
                  <p className="text-[10px] uppercase tracking-wider text-gray-400 font-bold mb-1">
                    Manual work assigned to
                  </p>
                  {manualTaskApprovals.length > 0 ? (
                    <div className="grid gap-2">
                      {manualTaskApprovals.map((task, tIdx) => (
                        <div key={tIdx} className="bg-white border border-gray-200 rounded p-2 text-sm space-y-1">
                          <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2">
                              {task.status === 'approved' ? (
                                <CheckCircle2 className="w-4 h-4 text-success" />
                              ) : task.status === 'rejected' ? (
                                <X className="w-4 h-4 text-alert" />
                              ) : (
                                <Circle className="w-4 h-4 text-gray-300" />
                              )}
                              <span className="font-medium text-gray-700">
                                {task.assignedToEmail || task.assignedToRole || 'Any Platform Admin'}
                              </span>
                            </div>
                            <span className={`text-xs font-medium ${
                              task.status === 'approved' ? 'text-success' :
                              task.status === 'rejected' ? 'text-alert' :
                              'text-gray-500'
                            }`}>
                              {task.status === 'approved' ? `Marked done by ${task.approvedBy || task.assignedToEmail || task.assignedToRole || 'Platform Admin'}` :
                               task.status === 'rejected' ? `Couldn't be completed by ${task.rejectedBy || task.assignedToEmail || task.assignedToRole || 'Platform Admin'}` :
                               task.dueAt ? `Waiting — due ${formatDate(task.dueAt)}` : 'Waiting'}
                            </span>
                          </div>
                          {task.instructions?.trim() && (
                            <p className="text-xs text-gray-600 whitespace-pre-wrap">{task.instructions}</p>
                          )}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="bg-white border border-gray-200 rounded p-2 text-sm italic text-gray-500">
                      Waiting for this task to be assigned.
                    </div>
                  )}
                </div>
              )}

              {/* Step Logs / Facts */}
              {((step.facts && step.facts.length > 0) || (isFailed && request.lastError)) && (
                <div className="ml-14 mt-3 space-y-2">
                  {isFailed && request.lastError && (
                    <div className="bg-red-50 border border-red-200 rounded p-3 text-red-800 text-xs flex items-start gap-2">
                      <AlertCircle className="w-4 h-4 shrink-0" />
                      <div>
                        <p className="font-bold mb-1">Error Details</p>
                        <p>{request.lastError.error || 'An unexpected error occurred during this step.'}</p>
                      </div>
                    </div>
                  )}

                  {step.facts && step.facts.length > 0 && (
                    <div className="bg-gray-50 rounded border border-gray-100 p-3">
                      <p className="text-[10px] uppercase tracking-wider text-gray-400 font-bold mb-2">Step Logs</p>
                      <div className="space-y-1.5">
                        {step.facts.map((fact, fIdx) => (
                          <div key={fIdx} className="flex items-start gap-2 text-xs">
                            <span className="text-gray-400 font-mono shrink-0">
                              [{formatInTimeZone(parseUtcDate(fact.timestamp), 'America/Los_Angeles', 'HH:mm:ss')}]
                            </span>
                            <div className="text-gray-700">
                              <span className="font-semibold text-gray-900">
                                {fact.type === 'rbac_exported' ? 'Exported for Terraform' : fact.type.replace(/_/g, ' ')}
                              </span>
                              {fact.type === 'rbac_exported' ? (
                                <>: <span>{(fact.data.files as unknown as string[] | undefined)?.join(', ')}</span></>
                              ) : fact.type === 'repo_created' ? (
                                <>: <a href={fact.data.repo_url} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline">
                                  {fact.data.repo_name}
                                </a></>
                              ) : fact.type === 'workspace_created' ? (
                                <>: <span className="font-mono bg-gray-100 px-1 rounded">{fact.data.workspace_url}</span></>
                              ) : fact.type === 'approval_received' ? (
                                <>: <span>Approved by <span className="font-medium">{fact.data.approved_by || fact.data.actor}</span></span></>
                              ) : fact.type === 'provisioning_failed' ? (
                                <>: <span className="text-red-600 font-medium">Failed: {fact.data.error}</span></>
                              ) : fact.type === 'request_rejected' ? (
                                <>
                                  : <span className="text-red-600 font-medium">Rejected by {fact.data.rejected_by}</span>
                                  {fact.data.rejection_note && (
                                    <div className="mt-1 p-2 bg-red-50 border border-red-100 rounded text-red-800 italic">
                                      "{fact.data.rejection_note}"
                                    </div>
                                  )}
                                </>
                              ) : null}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
