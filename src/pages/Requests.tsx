import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useRequestStore } from '../stores/requestStore';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { formatInTimeZone } from 'date-fns-tz';
import { Eye, X, Trash2, CheckCircle2, Circle, Loader2, AlertCircle, Clock } from 'lucide-react';
import type { Request } from '../types';

import { formatDistanceToNow, differenceInHours } from 'date-fns';

export const parseUtcDate = (dateString: string) => {
  if (!dateString) return new Date();
  // If string doesn't end with Z and doesn't have timezone offset, assume UTC and append Z
  if (!dateString.endsWith('Z') && !/[+-]\d{2}:?\d{2}$/.test(dateString)) {
    return new Date(dateString + 'Z');
  }
  return new Date(dateString);
};

const formatDate = (dateString: string) => {
  if (!dateString) return '';
  return formatInTimeZone(parseUtcDate(dateString), 'America/Los_Angeles', 'MMM d, yyyy h:mm a zzz');
};

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

  useEffect(() => {
    fetchApprovals();
  }, [fetchApprovals]);

  // Collect all states to display
  let steps: { id: string; name: string; status: string; type?: string; order: number; completedAt?: string; facts?: any[] }[] = [];

  // Always add "User Request" as the first step
  steps.push({
    id: 'user_request',
    name: 'User Request',
    status: 'completed',
    type: 'Initialization',
    order: 0,
    completedAt: request.createdAt
  });

  // Use the new linear states structure
  if (request.stateMachine.states && request.stateMachine.states.length > 0) {
    const stateSteps = request.stateMachine.states
      .filter(state => {
        // Skip terminal success state if we want a cleaner list
        // Or if it's currently active but previous step was also "completed"
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
          order: index + 1,
          completedAt: state.completedAt,
          facts: state.facts
        };
      });

    // Fallback: If request is failed but we have no states (e.g. failed to load state machine),
    // add a synthetic failed step so the error is visible.
    if (request.status === 'failed' && stateSteps.length === 0) {
      stateSteps.push({
        id: 'system_failure',
        name: 'System Processing',
        status: 'failed',
        order: 1,
        completedAt: request.updatedAt,
        facts: []
      });
    }

    // Fix for "failed" state visualization:
    // If request is globally failed, but no step is marked as "failed" (because checks above failed to match isActive),
    // then assume the first "pending" step is the one that failed.
    if (request.status === 'failed') {
      const hasFailedStep = stateSteps.some(s => s.status === 'failed');
      if (!hasFailedStep) {
        const firstPendingIdx = stateSteps.findIndex(s => s.status === 'pending');
        if (firstPendingIdx !== -1) {
          stateSteps[firstPendingIdx].status = 'failed';
        } else if (stateSteps.length > 0) {
          // If all are completed (rare for failed) or something else, mark the last one?
          // For now, only targeting pending steps.
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
          const isUserRequest = step.id === 'user_request';

          return (
            <div
              key={`${step.id}-${idx}`}
              className={`p-4 flex flex-col transition-colors ${isFailed ? 'bg-red-50 border-l-4 border-l-red-600' :
                isRejected ? 'bg-red-50 border-l-4 border-l-red-500' :
                  isActive ? 'bg-blue-50 border-l-4 border-l-blue-500' :
                    isCompleted ? 'bg-green-50 border-l-4 border-l-green-500' :
                      'bg-white border-l-4 border-l-gray-200'
                } first:rounded-t-lg last:rounded-b-lg hover:bg-opacity-80`}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-4 flex-1">
                  <div className={`w-10 h-10 rounded-full flex items-center justify-center flex-shrink-0 ${isFailed ? 'bg-red-600 text-white' :
                    isRejected ? 'bg-red-500 text-white' :
                      isCompleted ? 'bg-green-500 text-white' :
                        isActive ? 'bg-blue-500 text-white' :
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
                        <span className="text-xs text-gray-600 bg-gray-100 px-2 py-0.5 rounded-full">
                          {step.type}
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-3 text-sm text-gray-600">
                      <span className={`font-medium ${isFailed ? 'text-red-800 font-bold' :
                        isRejected ? 'text-red-700' :
                          isCompleted ? 'text-green-700' :
                            isActive ? 'text-blue-700' :
                              'text-gray-500'
                        }`}>
                        {isFailed ? 'Failed' :
                          isCompleted ? 'Completed' :
                            isRejected ? 'Rejected' :
                              isActive ? 'In Progress' :
                                'Pending'}
                      </span>
                      {(step.completedAt || (isUserRequest && request.createdAt)) && (
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
                <div className="ml-14">
                  <ProvisioningProgress progress={request.stateMachine.currentProgress} />
                </div>
              )}

              {/* Step Logs / Facts */}
              {((step.facts && step.facts.length > 0) || (isFailed && request.lastError)) && (
                <div className="ml-14 mt-3 space-y-2">
                  {isFailed && request.lastError && (
                    <div className="bg-red-50 border border-red-200 rounded p-3 text-red-800 text-xs flex items-start gap-2">
                      <AlertCircle className="w-4 h-4 flex-shrink-0" />
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
                            <span className="text-gray-400 font-mono flex-shrink-0">
                              [{formatInTimeZone(parseUtcDate(fact.timestamp), 'America/Los_Angeles', 'HH:mm:ss')}]
                            </span>
                            <div className="text-gray-700">
                              <span className="font-semibold text-gray-900">{fact.type.replace(/_/g, ' ')}</span>
                              {fact.type === 'repo_created' ? (
                                <>: <a href={fact.data.repo_url} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline">
                                  {fact.data.repo_name}
                                </a></>
                              ) : fact.type === 'workspace_created' ? (
                                <>: <span className="font-mono bg-gray-100 px-1 rounded">{fact.data.workspace_url}</span></>
                              ) : fact.type === 'approval_received' ? (
                                <>: <span>Approved by <span className="font-medium">{fact.data.actor}</span></span></>
                              ) : fact.type === 'provisioning_failed' ? (
                                <>: <span className="text-red-600 font-medium">Failed: {fact.data.error}</span></>
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

export function Requests() {
  const requests = useRequestStore((state) => state.requests);
  const fetchRequests = useRequestStore((state) => state.fetchRequests);
  const deleteRequest = useRequestStore((state) => state.deleteRequest);
  const { requestId } = useParams();
  const navigate = useNavigate();
  const [selectedRequestId, setSelectedRequestId] = useState<string | null>(requestId || null);
  const [isDeleting, setIsDeleting] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<'status' | 'conversation'>('status');
  const [filterStatus, setFilterStatus] = useState<'pending' | 'completed' | 'failed'>('pending');

  // Derived state: sort and filter
  const filteredRequests = requests
    .filter((r) => {
      if (filterStatus === 'pending') return !['completed', 'rejected', 'failed'].includes(r.status);
      if (filterStatus === 'completed') return r.status === 'completed';
      if (filterStatus === 'failed') return ['rejected', 'failed'].includes(r.status);
      return true;
    })
    .sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime());

  useEffect(() => {
    if (requestId) {
      setSelectedRequestId(requestId);
    } else {
      setSelectedRequestId(null);
    }
  }, [requestId]);

  // Derived state: get the full object from the store's list
  const selectedRequest = requests.find(r => r.id === selectedRequestId) || null;

  useEffect(() => {
    fetchRequests();
  }, [fetchRequests]);


  const handleDelete = async (requestId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (window.confirm('Are you sure you want to delete this request? This action cannot be undone.')) {
      setIsDeleting(requestId);
      try {
        await deleteRequest(requestId);
      } finally {
        setIsDeleting(null);
      }
    }
  };



  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between bg-white p-4 rounded-xl shadow-sm border border-gray-100">
        <h1 className="text-3xl font-bold text-gray-900">My Requests</h1>
        <div className="flex bg-gray-100 p-1 rounded-full border border-gray-200 shadow-inner">
          <button
            onClick={() => setFilterStatus('pending')}
            className={`px-6 py-2 rounded-full text-sm font-bold transition-all duration-200 ${filterStatus === 'pending'
              ? 'bg-white text-primary shadow-md transform scale-105'
              : 'text-gray-500 hover:text-gray-700'
              }`}
          >
            Pending
          </button>
          <button
            onClick={() => setFilterStatus('completed')}
            className={`px-6 py-2 rounded-full text-sm font-bold transition-all duration-200 ${filterStatus === 'completed'
              ? 'bg-white text-green-600 shadow-md transform scale-105'
              : 'text-gray-500 hover:text-gray-700'
              }`}
          >
            Completed
          </button>
          <button
            onClick={() => setFilterStatus('failed')}
            className={`px-6 py-2 rounded-full text-sm font-bold transition-all duration-200 ${filterStatus === 'failed'
              ? 'bg-white text-red-600 shadow-md transform scale-105'
              : 'text-gray-500 hover:text-gray-700'
              }`}
          >
            Failed
          </button>
        </div>
      </div>

      {/* Table View */}
      <Card>
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-gray-200 bg-gray-50">
                  <th className="text-left py-3 px-4 text-sm font-semibold text-gray-700">Title</th>
                  <th className="text-left py-3 px-4 text-sm font-semibold text-gray-700">Type</th>
                  <th className="text-left py-3 px-4 text-sm font-semibold text-gray-700">Status</th>
                  <th className="text-left py-3 px-4 text-sm font-semibold text-gray-700">Created</th>
                  <th className="text-left py-3 px-4 text-sm font-semibold text-gray-700">Updated</th>
                  <th className="text-left py-3 px-4 text-sm font-semibold text-gray-700">Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredRequests.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="py-20 text-center">
                      <div className="flex flex-col items-center justify-center space-y-3">
                        <div className="w-12 h-12 bg-gray-50 rounded-full flex items-center justify-center">
                          <Clock className="w-6 h-6 text-gray-400" />
                        </div>
                        <div>
                          <p className="text-lg font-semibold text-gray-900">No requests found</p>
                          <p className="text-sm text-gray-500">There are no requests matching the "{filterStatus}" filter.</p>
                        </div>
                      </div>
                    </td>
                  </tr>
                ) : (
                  filteredRequests.map((request) => (
                    <tr key={request.id} className="border-b border-gray-100 hover:bg-gray-50">
                      <td className="py-3 px-4 text-sm text-gray-900">{request.title}</td>
                      <td className="py-3 px-4 text-sm text-gray-600">
                        {request.type.replace(/_/g, ' ')}
                      </td>
                      <td className="py-3 px-4">
                        <span className={`px-2 py-1 rounded-full text-xs font-medium ${request.status === 'completed' ? 'bg-green-100 text-green-800' :
                          request.status === 'provisioning' ? 'bg-blue-100 text-blue-800' :
                            request.status === 'manager_approval' ? 'bg-yellow-100 text-yellow-800' :
                              request.status === 'training_pending' ? 'bg-orange-100 text-orange-800' :
                                ['failed', 'rejected'].includes(request.status) ? 'bg-red-100 text-red-800' :
                                  'bg-gray-100 text-gray-800'
                          }`}>
                          {request.status.replace(/_/g, ' ')}
                        </span>
                      </td>
                      <td className="py-3 px-4 text-sm text-gray-600">
                        {formatDate(request.createdAt)}
                      </td>
                      <td className="py-3 px-4 text-sm text-gray-600">
                        {formatDate(request.updatedAt)}
                      </td>
                      <td className="py-3 px-4">
                        <div className="flex items-center gap-2">
                          <Button
                            onClick={() => navigate(`/requests/${request.id}`)}
                            variant="outline"
                            size="sm"
                            className="flex items-center gap-2"
                          >
                            <Eye className="w-4 h-4" />
                            View
                          </Button>
                          <Button
                            onClick={(e) => handleDelete(request.id, e)}
                            variant="outline"
                            size="sm"
                            className="text-red-600 hover:text-red-700 hover:bg-red-50 border-red-200"
                            disabled={isDeleting === request.id}
                            title="Delete Request"
                          >
                            {isDeleting === request.id ? (
                              <Loader2 className="w-4 h-4 animate-spin" />
                            ) : (
                              <Trash2 className="w-4 h-4" />
                            )}
                          </Button>
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      {/* Flow Detail Modal */}
      {selectedRequest && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4" onClick={() => navigate('/requests')}>
          <div onClick={(e) => e.stopPropagation()} className="w-full max-w-6xl max-h-[90vh] flex flex-col">
            <Card className="w-full h-full overflow-hidden flex flex-col bg-white">
              <CardHeader className="flex-shrink-0 border-b border-gray-200">
                <div className="flex items-start justify-between">
                  <div>
                    <CardTitle>{selectedRequest.title}</CardTitle>
                    <p className="text-sm text-gray-500 mt-1">
                      Created {formatDate(selectedRequest.createdAt)}
                    </p>
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => navigate('/requests')}
                    className="flex items-center gap-2"
                  >
                    <X className="w-4 h-4" />
                    Close
                  </Button>
                </div>
              </CardHeader>
              <CardContent className="flex-1 overflow-y-auto space-y-6 p-6">
                {/* Tabs */}
                <div className="flex items-center gap-4 border-b border-gray-200 mb-6">
                  <button
                    onClick={() => setActiveTab('status')}
                    className={`pb-3 px-1 text-sm font-medium border-b-2 transition-colors ${activeTab === 'status'
                      ? 'border-primary text-primary'
                      : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                      }`}
                  >
                    Request Status
                  </button>
                  {selectedRequest.conversation && selectedRequest.conversation.length > 0 && (
                    <button
                      onClick={() => setActiveTab('conversation')}
                      className={`pb-3 px-1 text-sm font-medium border-b-2 transition-colors ${activeTab === 'conversation'
                        ? 'border-primary text-primary'
                        : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                        }`}
                    >
                      Conversation History
                    </button>
                  )}
                </div>

                {activeTab === 'status' ? (
                  <div>
                    <RequestStateList request={selectedRequest} />
                  </div>
                ) : (
                  <div className="space-y-4">
                    {selectedRequest.conversation?.map((message, idx) => (
                      <div
                        key={idx}
                        className={`flex ${message.type === 'user' ? 'justify-end' : 'justify-start'}`}
                      >
                        <div
                          className={`max-w-[80%] rounded-2xl px-4 py-3 shadow-sm ${message.type === 'user'
                            ? 'bg-primary text-white'
                            : 'bg-gray-50 text-gray-900 border border-gray-200/50'
                            }`}
                        >
                          {message.type === 'agent' ? (
                            <div
                              className="text-sm leading-relaxed prose prose-sm max-w-none [&_a]:text-blue-600 [&_a]:underline [&_a]:underline-offset-2 [&_a:hover]:text-blue-700 [&_a:visited]:text-purple-600"
                              dangerouslySetInnerHTML={{ __html: message.content }}
                            />
                          ) : (
                            <p className="text-sm leading-relaxed">{message.content}</p>
                          )}
                          <p className={`text-[10px] mt-1 ${message.type === 'user' ? 'text-blue-100' : 'text-gray-400'}`}>
                            {formatDate(typeof message.timestamp === 'string' ? message.timestamp : message.timestamp?.toISOString())}
                          </p>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        </div>
      )}
    </div>
  );
}
