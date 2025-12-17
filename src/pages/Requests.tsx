import { useState, useEffect } from 'react';
import { useRequestStore } from '../stores/requestStore';
import { RequestStatusFlow } from '../components/RequestStatusFlow';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { formatInTimeZone } from 'date-fns-tz';
import { Eye, X, Trash2, CheckCircle2, Circle, Loader2, AlertCircle } from 'lucide-react';
import type { Request } from '../types';

import { formatDistanceToNow, differenceInHours } from 'date-fns';

const parseUtcDate = (dateString: string) => {
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

function RequestStateList({ request }: { request: Request }) {
  const approvals = useRequestStore((state) => state.approvals);
  const fetchApprovals = useRequestStore((state) => state.fetchApprovals);

  useEffect(() => {
    fetchApprovals();
  }, [fetchApprovals]);

  // Collect all states to display
  let steps: { id: string; name: string; status: string; type?: string; order: number; completedAt?: string }[] = [];

  // Always add "User Request" as the first step
  steps.push({
    id: 'user_request',
    name: 'User Request',
    status: 'completed',
    type: 'Initialization',
    order: 0,
    completedAt: request.createdAt
  });

  if (request.stateMachine.parallelPaths.length > 0) {
    const pathSteps = request.stateMachine.parallelPaths.flatMap(path => 
      path.states.map(state => {
        let completedAt = undefined;
        // Try to find matching approval timestamp
        if (state.id.includes('approval') || state.status === 'completed') {
           // Look for an approved approval for this request
           const relevantApproval = approvals.find(a => 
             a.requestId === request.id && 
             a.status === 'approved' &&
             (
               state.id.toLowerCase().includes(a.approvalType.toLowerCase()) || 
               state.name.toLowerCase().includes(a.approvalType.toLowerCase())
             )
           );
           if (relevantApproval) {
             completedAt = relevantApproval.updatedAt;
           }
        }

        return {
          id: state.id,
          name: state.name,
          status: state.status,
          type: path.name,
          order: state.order,
          completedAt
        };
      })
    );
    steps = [...steps, ...pathSteps];
  } else {
    // Fallback for linear flows
    const allStates = ['pending', ...request.stateMachine.activeStates, ...request.stateMachine.completedStates];
    const uniqueStates = Array.from(new Set(allStates));
    const linearSteps = uniqueStates.map((id, index) => ({
      id,
      name: id.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase()),
      status: request.stateMachine.completedStates.includes(id) ? 'completed' : 
              request.stateMachine.activeStates.includes(id) ? 'active' : 'pending',
      order: index + 1
    }));
    steps = [...steps, ...linearSteps];
  }

  return (
    <div className="mb-6 space-y-4">
      <div className="bg-gray-50 rounded-lg border border-gray-200 divide-y divide-gray-200">
        {steps.map((step, idx) => {
          let isCompleted = step.status === 'completed';
          const isActive = step.status === 'active';
          const isTraining = step.id === 'training_pending';
          const isUserRequest = step.id === 'user_request';

          return (
            <div key={`${step.id}-${idx}`} className="p-3 flex items-center justify-between bg-white first:rounded-t-lg last:rounded-b-lg">
              <div className="flex items-center gap-3">
                <div className={`w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 ${
                  isCompleted ? 'bg-green-100 text-green-600' : 
                  isActive ? 'bg-blue-100 text-blue-600' : 'bg-gray-100 text-gray-400'
                }`}>
                  {isCompleted ? <CheckCircle2 className="w-5 h-5" /> : 
                   isActive ? <Loader2 className="w-5 h-5 animate-spin" /> : 
                   <Circle className="w-5 h-5" />}
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <p className="text-sm font-medium text-gray-900">{step.name}</p>
                    {step.type && <span className="text-xs text-gray-500 bg-gray-100 px-2 py-0.5 rounded-full">{step.type}</span>}
                  </div>
                  <div className="flex items-center gap-2 text-xs text-gray-500">
                    <span className="capitalize">Status: {step.status}</span>
                    {(isUserRequest || (isCompleted && step.completedAt)) && (
                      <>
                        <span>•</span>
                        <span>{formatDate(step.completedAt || request.createdAt)} ({formatDuration(step.completedAt || request.createdAt)})</span>
                      </>
                    )}
                  </div>
                </div>
              </div>

              {isTraining && isActive && !isCompleted && (
                <div className="flex items-center gap-2">
                   <div className="text-xs text-yellow-700 bg-yellow-50 px-2 py-1 rounded border border-yellow-200 flex items-center gap-1">
                     <AlertCircle className="w-3 h-3" />
                     Action Required
                   </div>
                   <TrainingAction requestId={request.id} />
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
  const [selectedRequestId, setSelectedRequestId] = useState<string | null>(null);
  const [isDeleting, setIsDeleting] = useState<string | null>(null);

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

  if (requests.length === 0) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="text-center">
          <p className="text-lg text-gray-600 mb-4">No requests yet</p>
          <p className="text-sm text-gray-500">
            Start by using the Agentic Helper on the home page to create a request.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-900 mb-2">My Requests</h1>
        <p className="text-gray-600">Track the status of your requests</p>
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
                {requests.map((request) => (
                  <tr key={request.id} className="border-b border-gray-100 hover:bg-gray-50">
                    <td className="py-3 px-4 text-sm text-gray-900">{request.title}</td>
                    <td className="py-3 px-4 text-sm text-gray-600">
                      {request.type.replace(/_/g, ' ')}
                    </td>
                    <td className="py-3 px-4">
                      <span className={`px-2 py-1 rounded-full text-xs font-medium ${
                        request.status === 'completed' ? 'bg-green-100 text-green-800' :
                        request.status === 'provisioning' ? 'bg-blue-100 text-blue-800' :
                        request.status === 'manager_approval' ? 'bg-yellow-100 text-yellow-800' :
                        request.status === 'training_pending' ? 'bg-orange-100 text-orange-800' :
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
                          onClick={() => setSelectedRequestId(request.id)}
                          variant="outline"
                          size="sm"
                          className="flex items-center gap-2"
                        >
                          <Eye className="w-4 h-4" />
                          View Flow
                        </Button>
                        <Button
                          onClick={(e) => handleDelete(request.id, e)}
                          variant="outline"
                          size="sm"
                          className="flex items-center gap-2 text-red-600 hover:text-red-700 hover:bg-red-50 border-red-200"
                          disabled={isDeleting === request.id}
                        >
                          <Trash2 className="w-4 h-4" />
                          {isDeleting === request.id ? '...' : 'Delete'}
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      {/* Flow Detail Modal */}
      {selectedRequest && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
          <Card className="w-full max-w-6xl max-h-[90vh] overflow-hidden flex flex-col bg-white">
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
                  onClick={() => setSelectedRequestId(null)}
                  className="flex items-center gap-2"
                >
                  <X className="w-4 h-4" />
                  Close
                </Button>
              </div>
            </CardHeader>
            <CardContent className="flex-1 overflow-y-auto space-y-6 p-6">
              <RequestStateList request={selectedRequest} />
              
              <div>
                <RequestStatusFlow 
                  stateMachine={selectedRequest.stateMachine} 
                  requestStatus={selectedRequest.status}
                />
              </div>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
