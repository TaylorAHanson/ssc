import { useState } from 'react';
import { useRequestStore } from '../stores/requestStore';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { CheckCircle2, UserPlus, Clock, Check, X, Shield, Database, Badge } from 'lucide-react';
import { format } from 'date-fns';
import type { Approval, ApprovalAction } from '../types';

export function Approvals() {
  const approvals = useRequestStore((state) => state.approvals);
  const processApproval = useRequestStore((state) => state.processApproval);
  const [selectedApproval, setSelectedApproval] = useState<Approval | null>(null);
  const [actionType, setActionType] = useState<'approve' | 'reject' | 'delegate' | null>(null);
  const [note, setNote] = useState('');
  const [delegateEmail, setDelegateEmail] = useState('');
  const [isProcessing, setIsProcessing] = useState(false);

  // User roles
  const userRoles = ['platform_admin', 'data_owner'];
  const roleLabels: Record<string, string> = {
    platform_admin: 'Platform Admin',
    data_owner: 'Data Owner',
  };

  // Filter approvals by user's roles
  const relevantApprovals = approvals.filter((a) => 
    userRoles.includes(a.approvalType) && a.status === 'pending'
  );
  const pendingApprovals = relevantApprovals;
  const completedApprovals = approvals.filter((a) => a.status !== 'pending');

  const handleAction = async (approval: Approval, action: 'approve' | 'reject' | 'delegate') => {
    setSelectedApproval(approval);
    setActionType(action);
    setNote('');
    setDelegateEmail('');
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
      note: actionType === 'reject' ? note : undefined,
      delegatedToEmail: actionType === 'delegate' ? delegateEmail : undefined,
    };

    await processApproval(action);
    
    setIsProcessing(false);
    setSelectedApproval(null);
    setActionType(null);
    setNote('');
    setDelegateEmail('');
  };

  // What user can approve based on roles
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
    ],
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-900 mb-2">Pending Approvals</h1>
        <p className="text-gray-600">Review and process requests requiring your approval</p>
      </div>

      {/* Role Section */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Badge className="w-5 h-5" />
            My Roles & Approval Capabilities
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {userRoles.map((role) => (
              <div key={role} className="border border-gray-200 rounded-lg p-4">
                <div className="flex items-center gap-2 mb-3">
                  {role === 'platform_admin' ? (
                    <Shield className="w-5 h-5 text-primary" />
                  ) : (
                    <Database className="w-5 h-5 text-primary" />
                  )}
                  <h3 className="font-semibold text-gray-900">{roleLabels[role]}</h3>
                </div>
                {role === 'data_owner' && (
                  <p className="text-sm text-gray-600 mb-2">Data Owner for: <strong>platform_catalog</strong></p>
                )}
                <ul className="space-y-1 text-sm text-gray-600">
                  {approvalCapabilities[role as keyof typeof approvalCapabilities].map((capability, idx) => (
                    <li key={idx} className="flex items-start gap-2">
                      <span className="text-primary mt-1">•</span>
                      <span>{capability}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
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
          {pendingApprovals.map((approval) => (
            <Card key={approval.id} className="border-l-4 border-l-primary">
              <CardHeader>
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <CardTitle className="text-lg">{approval.requestTitle}</CardTitle>
                    <div className="mt-2 space-y-1 text-sm text-gray-600">
                      <p>
                        <span className="font-medium">Requested by:</span> {approval.requestedBy} ({approval.requestedByEmail})
                      </p>
                      <p>
                        <span className="font-medium">Type:</span> {approval.requestType.replace(/_/g, ' ')}
                      </p>
                      <p>
                        <span className="font-medium">Approval Type:</span>{' '}
                        <span className={`px-2 py-1 rounded-full text-xs font-medium ${
                          approval.approvalType === 'platform_admin' 
                            ? 'bg-blue-100 text-blue-800' 
                            : approval.approvalType === 'data_owner'
                            ? 'bg-purple-100 text-purple-800'
                            : 'bg-gray-100 text-gray-800'
                        }`}>
                          {roleLabels[approval.approvalType] || approval.approvalType.replace(/_/g, ' ')}
                        </span>
                      </p>
                      <p className="flex items-center gap-1">
                        <Clock className="w-4 h-4" />
                        <span>Requested {format(new Date(approval.createdAt), 'PPp')}</span>
                      </p>
                    </div>
                  </div>
                </div>
              </CardHeader>
              <CardContent>
                <div className="flex gap-2">
                  <Button
                    onClick={() => handleAction(approval, 'approve')}
                    className="bg-green-600 hover:bg-green-700 text-white"
                  >
                    <Check className="w-4 h-4 mr-2" />
                    Approve
                  </Button>
                  <Button
                    onClick={() => handleAction(approval, 'reject')}
                    variant="outline"
                    className="border-red-300 text-red-700 hover:bg-red-50"
                  >
                    <X className="w-4 h-4 mr-2" />
                    Reject
                  </Button>
                  <Button
                    onClick={() => handleAction(approval, 'delegate')}
                    variant="outline"
                    className="border-blue-300 text-blue-700 hover:bg-blue-50"
                  >
                    <UserPlus className="w-4 h-4 mr-2" />
                    Delegate
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
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
                        <span className={`px-2 py-1 rounded-full text-xs font-medium ${
                          approval.status === 'approved' ? 'bg-green-100 text-green-800' :
                          approval.status === 'rejected' ? 'bg-red-100 text-red-800' :
                          'bg-blue-100 text-blue-800'
                        }`}>
                          {approval.status === 'approved' ? 'Approved' :
                           approval.status === 'rejected' ? 'Rejected' :
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
                      <p className="text-xs text-gray-500">
                        {format(new Date(approval.updatedAt), 'PPp')}
                      </p>
                    </div>
                  </div>
                </div>
              </CardHeader>
            </Card>
          ))}
        </div>
      )}

      {/* Action Modal */}
      {selectedApproval && actionType && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <Card className="w-full max-w-md mx-4">
            <CardHeader>
              <CardTitle>
                {actionType === 'approve' && 'Approve Request'}
                {actionType === 'reject' && 'Reject Request'}
                {actionType === 'delegate' && 'Delegate Approval'}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="text-sm text-gray-600">
                <span className="font-medium">Request:</span> {selectedApproval.requestTitle}
              </p>
              
              {actionType === 'reject' && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">
                    Rejection Note <span className="text-red-500">*</span>
                  </label>
                  <Input
                    type="text"
                    placeholder="Please provide a reason for rejection..."
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    className="w-full"
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
                    placeholder="user@qualcomm.com"
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
                  disabled={isProcessing || (actionType === 'reject' && !note.trim()) || (actionType === 'delegate' && !delegateEmail.trim())}
                  className={actionType === 'approve' ? 'bg-green-600 hover:bg-green-700' : 
                              actionType === 'reject' ? 'bg-red-600 hover:bg-red-700' :
                              'bg-blue-600 hover:bg-blue-700'}
                >
                  {isProcessing ? 'Processing...' : 
                   actionType === 'approve' ? 'Confirm Approval' :
                   actionType === 'reject' ? 'Confirm Rejection' :
                   'Confirm Delegation'}
                </Button>
                <Button
                  variant="outline"
                  onClick={() => {
                    setSelectedApproval(null);
                    setActionType(null);
                    setNote('');
                    setDelegateEmail('');
                  }}
                  disabled={isProcessing}
                >
                  Cancel
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}

