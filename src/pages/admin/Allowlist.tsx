import { useState, useEffect } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Textarea } from '../../components/ui/textarea';
import { Loader2, Plus, ShieldCheck, Trash2, Edit } from 'lucide-react';
import { getAllowlist, createAllowlistEntry, deleteAllowlistEntry, updateAllowlistEntry, getTargetWorkspaces } from '../../services/api';
import type { AllowlistEntry, AllowlistCreate, TargetWorkspace } from '../../services/api';
import { format, parseISO } from 'date-fns';

export function Allowlist() {
  const [entries, setEntries] = useState<AllowlistEntry[]>([]);
  const [targetWorkspaces, setTargetWorkspaces] = useState<TargetWorkspace[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  // Form state
  const [resourceId, setResourceId] = useState('');
  const [resourceType, setResourceType] = useState('app');
  const [workspace, setWorkspace] = useState('');
  const [justification, setJustification] = useState('');
  const [status, setStatus] = useState<'pending' | 'approved' | 'rejected'>('approved');
  const [expiresAt, setExpiresAt] = useState('');

  const loadEntries = async () => {
    setIsLoading(true);
    try {
      const data = await getAllowlist();
      setEntries(data);
    } catch (error) {
      console.error('Failed to load allowlist:', error);
      setMessage({ type: 'error', text: 'Failed to load allowlist entries' });
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadEntries();
    getTargetWorkspaces()
      .then(res => setTargetWorkspaces(res.workspaces || []))
      .catch(err => console.error('Failed to load target workspaces:', err));
  }, []);

  const resetForm = () => {
    setResourceId('');
    setResourceType('app');
    setWorkspace('');
    setJustification('');
    setStatus('approved');
    setExpiresAt('');
    setEditingId(null);
    setShowForm(false);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setMessage(null);

    try {
      if (editingId) {
        await updateAllowlistEntry(editingId, {
          justification,
          status,
          expires_at: expiresAt || undefined
        });
        setMessage({ type: 'success', text: 'Entry updated successfully' });
      } else {
        const payload: AllowlistCreate = {
          resource_id: resourceId,
          resource_type: resourceType,
          workspace,
          justification,
          status,
          expires_at: expiresAt ? new Date(expiresAt).toISOString() : undefined
        };
        await createAllowlistEntry(payload);
        setMessage({ type: 'success', text: 'Entry created successfully' });
      }
      
      resetForm();
      await loadEntries();
      
      setTimeout(() => setMessage(null), 3000);
    } catch (error) {
      setMessage({ type: 'error', text: `Error: ${error instanceof Error ? error.message : 'Unknown error'}` });
    }
  };

  const handleEdit = (entry: AllowlistEntry) => {
    setResourceId(entry.resource_id);
    setResourceType(entry.resource_type);
    setWorkspace(entry.workspace);
    setJustification(entry.justification);
    setStatus(entry.status);
    setExpiresAt(entry.expires_at ? new Date(entry.expires_at).toISOString().slice(0, 16) : '');
    setEditingId(entry.id);
    setShowForm(true);
  };

  const handleDelete = async (id: string) => {
    if (!confirm('Are you sure you want to delete this allowlist exception? The resource may be killed by the Sentinel.')) return;
    
    try {
      await deleteAllowlistEntry(id);
      setMessage({ type: 'success', text: 'Entry deleted successfully' });
      await loadEntries();
      setTimeout(() => setMessage(null), 3000);
    } catch (error) {
      setMessage({ type: 'error', text: `Failed to delete: ${error instanceof Error ? error.message : 'Unknown error'}` });
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-gray-900 flex items-center gap-2">
            <ShieldCheck className="w-5 h-5" /> Enforcement Allowlist
          </h2>
          <p className="text-gray-500 text-sm mt-1">Manage governance exceptions for resources across workspaces.</p>
        </div>
        {!showForm && (
          <Button onClick={() => setShowForm(true)} className="flex items-center gap-2">
            <Plus className="w-4 h-4" /> Add Exception
          </Button>
        )}
      </div>

      {message && (
        <div className={`p-3 rounded-md ${message.type === 'success' ? 'bg-green-50 text-green-800 border border-green-200' : 'bg-red-50 text-red-800 border border-red-200'}`}>
          {message.text}
        </div>
      )}

      {showForm && (
        <Card className="border-primary/20 shadow-md">
          <CardHeader className="bg-gray-50/50 border-b">
            <CardTitle className="text-lg">{editingId ? 'Edit Exception' : 'Add New Exception'}</CardTitle>
          </CardHeader>
          <CardContent className="pt-6">
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <label className="text-sm font-medium">Resource ID (e.g. app name)</label>
                  <Input 
                    value={resourceId} 
                    onChange={(e) => setResourceId(e.target.value)} 
                    placeholder="my-cool-app" 
                    required 
                    disabled={!!editingId} 
                  />
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium">Resource Type</label>
                  <select 
                    value={resourceType} 
                    onChange={(e) => setResourceType(e.target.value)}
                    className="w-full h-10 px-3 border border-gray-300 rounded-md bg-white text-sm"
                    disabled={!!editingId}
                  >
                    <option value="app">Databricks App</option>
                    <option value="notebook">Notebook</option>
                    <option value="job">Job</option>
                    <option value="dashboard">Dashboard</option>
                    <option value="genie_space">Genie Space</option>
                  </select>
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium">Workspace</label>
                  <select
                    value={workspace}
                    onChange={(e) => setWorkspace(e.target.value)}
                    required
                    disabled={!!editingId}
                    className="w-full h-10 px-3 border border-gray-300 rounded-md bg-white text-sm disabled:opacity-50"
                  >
                    <option value="" disabled>Select a workspace…</option>
                    {targetWorkspaces.map(w => (
                      <option key={w.name} value={w.name}>
                        {w.name}{w.environment ? ` (${w.environment})` : ''}
                      </option>
                    ))}
                    {/* Preserve a legacy/custom value not in the current target list. */}
                    {workspace && !targetWorkspaces.some(w => w.name === workspace) && (
                      <option value={workspace}>{workspace}</option>
                    )}
                  </select>
                  <p className="text-[11px] text-gray-500">
                    Must match a target workspace name so the Sentinel applies this exception when it scans that workspace.
                  </p>
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium">Status</label>
                  <select 
                    value={status} 
                    onChange={(e) => setStatus(e.target.value as any)}
                    className="w-full h-10 px-3 border border-gray-300 rounded-md bg-white text-sm"
                  >
                    <option value="approved">Approved</option>
                    <option value="pending">Pending</option>
                    <option value="rejected">Rejected</option>
                  </select>
                </div>
              </div>
              
              <div className="space-y-2">
                <label className="text-sm font-medium">Justification</label>
                <Textarea 
                  value={justification} 
                  onChange={(e) => setJustification(e.target.value)} 
                  placeholder="Required for financial forecasting compliance..." 
                  required 
                />
              </div>

              <div className="space-y-2">
                <label className="text-sm font-medium">Expires At (Optional)</label>
                <Input 
                  type="datetime-local" 
                  value={expiresAt} 
                  onChange={(e) => setExpiresAt(e.target.value)} 
                />
              </div>

              <div className="flex justify-end gap-2 pt-4">
                <Button type="button" variant="outline" onClick={resetForm}>Cancel</Button>
                <Button type="submit">{editingId ? 'Save Changes' : 'Create Exception'}</Button>
              </div>
            </form>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardContent className="p-0">
          {isLoading ? (
            <div className="py-12 flex justify-center">
              <Loader2 className="w-8 h-8 animate-spin text-gray-400" />
            </div>
          ) : entries.length === 0 ? (
            <div className="py-12 text-center text-gray-500">
              No allowlist exceptions found.
            </div>
          ) : (
            <table className="w-full text-sm text-left">
              <thead className="bg-gray-50 text-gray-900 font-medium">
                <tr>
                  <th className="p-3">Resource</th>
                  <th className="p-3">Workspace</th>
                  <th className="p-3">Status</th>
                  <th className="p-3">Justification</th>
                  <th className="p-3">Expires</th>
                  <th className="p-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {entries.map((entry) => (
                  <tr key={entry.id} className="hover:bg-gray-50">
                    <td className="p-3">
                      <div className="font-medium text-gray-900">{entry.resource_id}</div>
                      <div className="text-xs text-gray-500 uppercase">{entry.resource_type}</div>
                    </td>
                    <td className="p-3 text-gray-600">{entry.workspace}</td>
                    <td className="p-3">
                      <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
                        entry.status === 'approved' ? 'bg-green-100 text-green-800' :
                        entry.status === 'pending' ? 'bg-amber-100 text-amber-800' :
                        'bg-red-100 text-red-800'
                      }`}>
                        {entry.status.toUpperCase()}
                      </span>
                    </td>
                    <td className="p-3 text-gray-600 max-w-[200px] truncate" title={entry.justification}>
                      {entry.justification}
                    </td>
                    <td className="p-3 text-gray-500">
                      {entry.expires_at ? format(parseISO(entry.expires_at), 'MMM d, yyyy') : 'Never'}
                    </td>
                    <td className="p-3 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button 
                          onClick={() => handleEdit(entry)}
                          className="p-1 text-gray-400 hover:text-blue-600 transition-colors"
                          title="Edit"
                        >
                          <Edit className="w-4 h-4" />
                        </button>
                        <button 
                          onClick={() => handleDelete(entry.id)}
                          className="p-1 text-gray-400 hover:text-red-600 transition-colors"
                          title="Delete"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}