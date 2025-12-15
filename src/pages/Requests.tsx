import { useState } from 'react';
import { useRequestStore } from '../stores/requestStore';
import { RequestStatusFlow } from '../components/RequestStatusFlow';
import { TrainingBlocker } from '../components/TrainingBlocker';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { format } from 'date-fns';
import { Eye, X } from 'lucide-react';
import type { Request } from '../types';

export function Requests() {
  const requests = useRequestStore((state) => state.requests);
  const [selectedRequest, setSelectedRequest] = useState<Request | null>(null);

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
                      {format(new Date(request.createdAt), 'MMM d, yyyy')}
                    </td>
                    <td className="py-3 px-4 text-sm text-gray-600">
                      {format(new Date(request.updatedAt), 'MMM d, yyyy')}
                    </td>
                    <td className="py-3 px-4">
                      <Button
                        onClick={() => setSelectedRequest(request)}
                        variant="outline"
                        size="sm"
                        className="flex items-center gap-2"
                      >
                        <Eye className="w-4 h-4" />
                        View Flow
                      </Button>
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
                    Created {format(new Date(selectedRequest.createdAt), 'PPp')}
                  </p>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setSelectedRequest(null)}
                  className="flex items-center gap-2"
                >
                  <X className="w-4 h-4" />
                  Close
                </Button>
              </div>
            </CardHeader>
            <CardContent className="flex-1 overflow-y-auto space-y-6 p-6">
              {selectedRequest.requiresTraining && (
                <TrainingBlocker
                  requestId={selectedRequest.id}
                  requiresTraining={selectedRequest.requiresTraining}
                  trainingCompleted={selectedRequest.trainingCompleted || false}
                />
              )}
              
              <div>
                <h3 className="text-sm font-semibold text-gray-700 mb-3">
                  Request Status Flow
                </h3>
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

