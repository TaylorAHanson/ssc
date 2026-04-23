import { useState, useEffect } from 'react';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../../components/ui/card';
import { FileText, Search, Edit, Trash2, CheckCircle2, AlertCircle, Plus, Loader2, Save, X } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { api } from '../../services/api';
import type { OdpsDocument, DataContract } from '../../services/api';
import { format, parseISO } from 'date-fns';
import Editor from '@monaco-editor/react';
import yaml from 'js-yaml';

export function ODPS() {
  const [odpsList, setOdpsList] = useState<OdpsDocument[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState('');
  
  // Modal states
  const [isDraftModalOpen, setIsDraftModalOpen] = useState(false);
  const [isEditorOpen, setIsEditorOpen] = useState(false);
  
  // Draft Form States
  const [availableContracts, setAvailableContracts] = useState<DataContract[]>([]);
  const [selectedContracts, setSelectedContracts] = useState<string[]>([]);
  const [openapiUrls, setOpenapiUrls] = useState<string[]>([]);
  const [newApiUrl, setNewApiUrl] = useState('');
  const [datasetSearch, setDatasetSearch] = useState('');
  const [productName, setProductName] = useState('');
  const [isDrafting, setIsDrafting] = useState(false);
  const [draftError, setDraftError] = useState<string | null>(null);

  // Editor States
  const [yamlContent, setYamlContent] = useState('');
  const [yamlError, setYamlError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [editingDoc, setEditingDoc] = useState<OdpsDocument | null>(null);
  
  const loadData = async () => {
    setIsLoading(true);
    try {
      const data = await api.getOdpsList();
      setOdpsList(data);
    } catch (e) {
      console.error('Failed to load ODPS documents', e);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const openDraftModal = async () => {
    setIsDraftModalOpen(true);
    setDraftError(null);
    setSelectedContracts([]);
    setOpenapiUrls([]);
    setNewApiUrl('');
    setDatasetSearch('');
    setProductName('');
    try {
      const contracts = await api.getDataContracts();
      // filter only active
      setAvailableContracts(contracts.filter(c => c.is_active));
    } catch (e) {
      console.error(e);
      setDraftError('Failed to load available data contracts.');
    }
  };

  const handleDraft = async () => {
    if (!productName.trim()) {
      setDraftError('Product name is required');
      return;
    }
    if (selectedContracts.length === 0) {
      setDraftError('Select at least one Data Contract');
      return;
    }
    
    setIsDrafting(true);
    setDraftError(null);
    try {
      const urlsToSubmit = newApiUrl.trim() ? [...openapiUrls, newApiUrl.trim()] : openapiUrls;
      const res = await api.draftOdps(selectedContracts, urlsToSubmit, productName);
      setYamlContent(res.yaml_content);
      setYamlError(null);
      setEditingDoc(null);
      setIsDraftModalOpen(false);
      setIsEditorOpen(true);
    } catch (e: any) {
      setDraftError(e.message || 'Failed to draft ODPS');
    } finally {
      setIsDrafting(false);
    }
  };

  const handleEdit = (doc: OdpsDocument) => {
    setEditingDoc(doc);
    setProductName(doc.name);
    setYamlContent(doc.yaml_content);
    setYamlError(null);
    setIsEditorOpen(true);
  };

  const handleDelete = async (id: string) => {
    if (!confirm('Are you sure you want to delete this ODPS document and all its versions?')) {
      return;
    }
    try {
      await api.deleteOdps(id);
      await loadData();
    } catch (e: any) {
      alert('Failed to delete ODPS: ' + e.message);
    }
  };

  const handleValidate = (value: string | undefined) => {
    const val = value || '';
    setYamlContent(val);
    try {
      yaml.load(val);
      setYamlError(null);
    } catch (e: any) {
      setYamlError(e.message);
    }
  };

  const handleSave = async () => {
    if (yamlError) return;
    setIsSaving(true);
    try {
      await api.saveOdps(productName, yamlContent);
      setIsEditorOpen(false);
      await loadData();
    } catch (e: any) {
      setYamlError(e.message || 'Failed to save ODPS');
    } finally {
      setIsSaving(false);
    }
  };

  const toggleContractSelection = (id: string) => {
    setSelectedContracts(prev => 
      prev.includes(id) ? prev.filter(c => c !== id) : [...prev, id]
    );
  };

  const processedList = odpsList.filter(doc => 
    doc.name.toLowerCase().includes(searchTerm.toLowerCase())
  );

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <FileText className="w-5 h-5 text-gray-700" />
            Data Products
          </CardTitle>
          <CardDescription>
            Manage your ODPS documents. Draft new ones by combining certified Open Data Contracts (ODCS) and API interfaces.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="mb-4 flex gap-4 items-center justify-between">
            <div className="relative w-full max-w-md">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
              <input
                type="text"
                placeholder="Search ODPS documents..."
                className="w-full pl-9 pr-4 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-primary focus:border-primary"
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
              />
            </div>
            <Button
              onClick={openDraftModal}
              className="flex items-center gap-2 bg-primary text-white"
            >
              <Plus className="w-4 h-4" />
              Draft New ODPS
            </Button>
          </div>

          <div className="overflow-x-auto rounded-lg border border-gray-200">
            <table className="w-full text-sm text-left">
              <thead className="bg-gray-50 text-gray-700 font-medium border-b border-gray-200">
                <tr>
                  <th className="p-3 pl-4">Product Name</th>
                  <th className="p-3">Version</th>
                  <th className="p-3">Status</th>
                  <th className="p-3">Created At</th>
                  <th className="p-3">Created By</th>
                  <th className="p-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 bg-white">
                {isLoading ? (
                  <tr>
                    <td colSpan={6} className="p-6 text-center text-gray-500">Loading ODPS documents...</td>
                  </tr>
                ) : processedList.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="p-6 text-center text-gray-500">No ODPS documents found.</td>
                  </tr>
                ) : (
                  processedList.map(doc => (
                    <tr key={doc.id} className="hover:bg-gray-50 transition-colors">
                      <td className="p-3 pl-4 font-medium text-gray-900">{doc.name}</td>
                      <td className="p-3 text-gray-600">v{doc.version}.0</td>
                      <td className="p-3">
                        {doc.is_active ? (
                          <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-green-100 text-green-800">
                            Active
                          </span>
                        ) : (
                          <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-800">
                            Inactive
                          </span>
                        )}
                      </td>
                      <td className="p-3 text-gray-600">{format(parseISO(doc.created_at), 'MMM d, yyyy HH:mm')}</td>
                      <td className="p-3 text-gray-600">{doc.created_by || 'System'}</td>
                      <td className="p-3 text-right">
                        <div className="flex items-center justify-end gap-2">
                          <Button 
                            variant="outline" 
                            size="sm" 
                            onClick={() => handleEdit(doc)}
                            className="text-xs h-7 px-2 border-blue-200 text-blue-600 hover:bg-blue-50"
                          >
                            <Edit className="w-3 h-3 mr-1" />
                            Edit
                          </Button>
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => handleDelete(doc.id)}
                            className="text-xs h-7 px-2 border-red-200 text-red-600 hover:bg-red-50"
                            title="Delete"
                          >
                            <Trash2 className="w-3 h-3" />
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

      {/* Draft Modal */}
      {isDraftModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4 animate-in fade-in">
          <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl overflow-hidden animate-in zoom-in-95">
            <div className="flex items-center justify-between p-4 border-b border-gray-100">
              <h3 className="text-lg font-semibold text-gray-900 flex items-center gap-2">
                <FileText className="w-5 h-5 text-blue-600" />
                Draft New ODPS
              </h3>
              <Button variant="ghost" size="sm" onClick={() => setIsDraftModalOpen(false)}>
                <X className="w-5 h-5 text-gray-500" />
              </Button>
            </div>
            <div className="p-6 space-y-6">
              {draftError && (
                <div className="p-3 rounded-lg bg-red-50 text-red-700 border border-red-200 flex items-center gap-2 text-sm">
                  <AlertCircle className="w-4 h-4" />
                  {draftError}
                </div>
              )}
              
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Product Name <span className="text-red-500">*</span></label>
                <input
                  type="text"
                  className="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-primary focus:border-primary sm:text-sm"
                  placeholder="e.g. Customer 360"
                  value={productName}
                  onChange={e => setProductName(e.target.value)}
                />
              </div>

              <div>
                <div className="flex items-center justify-between mb-2">
                  <label className="block text-sm font-medium text-gray-700">Add Dataset <span className="text-red-500">*</span></label>
                  <div className="relative w-64">
                    <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3 h-3 text-gray-400" />
                    <input
                      type="text"
                      className="w-full pl-7 pr-2 py-1 border border-gray-300 rounded text-xs focus:ring-primary focus:border-primary"
                      placeholder="Search datasets..."
                      value={datasetSearch}
                      onChange={e => setDatasetSearch(e.target.value)}
                    />
                  </div>
                </div>
                <div className="max-h-48 overflow-y-auto border border-gray-200 rounded-md p-2 space-y-1 bg-white">
                  {availableContracts.length === 0 ? (
                    <p className="text-sm text-gray-500 italic p-2">No active datasets available.</p>
                  ) : (
                    availableContracts
                      .filter(c => c.dataset_id.toLowerCase().includes(datasetSearch.toLowerCase()))
                      .map(c => (
                      <label key={c.id} className="flex items-center gap-2 p-2 hover:bg-gray-50 rounded cursor-pointer">
                        <input
                          type="checkbox"
                          checked={selectedContracts.includes(c.dataset_id)}
                          onChange={() => toggleContractSelection(c.dataset_id)}
                          className="rounded text-primary focus:ring-primary"
                        />
                        <span className="text-sm text-gray-900 font-mono">{c.dataset_id}</span>
                      </label>
                    ))
                  )}
                </div>
                {selectedContracts.length > 0 && (
                  <p className="text-xs text-gray-500 mt-1 font-medium">{selectedContracts.length} dataset(s) selected.</p>
                )}
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Add API (OpenAPI Specification URL)</label>
                <div className="flex gap-2 mb-2">
                  <input
                    type="text"
                    className="flex-1 px-3 py-2 border border-gray-300 rounded-md focus:ring-primary focus:border-primary sm:text-sm"
                    placeholder="https://api.example.com/openapi.json"
                    value={newApiUrl}
                    onChange={e => setNewApiUrl(e.target.value)}
                    onKeyDown={e => {
                      if (e.key === 'Enter') {
                        e.preventDefault();
                        if (newApiUrl.trim()) {
                          setOpenapiUrls([...openapiUrls, newApiUrl.trim()]);
                          setNewApiUrl('');
                        }
                      }
                    }}
                  />
                  <Button 
                    variant="outline" 
                    onClick={() => {
                      if (newApiUrl.trim()) {
                        setOpenapiUrls([...openapiUrls, newApiUrl.trim()]);
                        setNewApiUrl('');
                      }
                    }}
                  >
                    Add
                  </Button>
                </div>
                <p className="text-xs text-gray-500 mb-2">Provide one or more URLs to OpenAPI specs to include API definitions in the ODPS.</p>
                
                {openapiUrls.length > 0 && (
                  <div className="space-y-2 mt-2">
                    {openapiUrls.map((url, idx) => (
                      <div key={idx} className="flex items-center justify-between bg-gray-50 p-2 rounded border border-gray-200">
                        <span className="text-sm text-gray-700 truncate mr-2">{url}</span>
                        <button 
                          className="text-red-500 hover:text-red-700 p-1 rounded hover:bg-red-50"
                          onClick={() => setOpenapiUrls(openapiUrls.filter((_, i) => i !== idx))}
                          title="Remove API"
                        >
                          <X className="w-4 h-4" />
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>

            </div>
            <div className="p-4 border-t border-gray-100 bg-gray-50 flex justify-end gap-3">
              <Button variant="outline" onClick={() => setIsDraftModalOpen(false)}>Cancel</Button>
              <Button onClick={handleDraft} disabled={isDrafting} className="bg-primary text-white">
                {isDrafting ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : null}
                {isDrafting ? 'Drafting with AI...' : 'Generate Draft'}
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Editor Modal */}
      {isEditorOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4 animate-in fade-in">
          <div className="bg-white rounded-xl shadow-xl w-full max-w-[95vw] xl:max-w-[1200px] h-[90vh] flex flex-col overflow-hidden animate-in zoom-in-95">
            <div className="flex items-center justify-between p-4 border-b border-gray-100 bg-white">
              <div>
                <h3 className="text-lg font-semibold text-gray-900 flex items-center gap-2">
                  <FileText className="w-5 h-5 text-blue-600" />
                  {editingDoc ? 'Edit ODPS Document' : 'Review Drafted ODPS Document'}
                </h3>
                <p className="text-xs text-gray-500 mt-1 font-mono">{productName}</p>
              </div>
              <div className="flex items-center gap-3">
                <Button 
                  variant="default" 
                  size="sm" 
                  disabled={!!yamlError || isSaving}
                  onClick={handleSave}
                  className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 text-white"
                >
                  {isSaving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                  Save ODPS
                </Button>
                <Button variant="ghost" size="sm" onClick={() => setIsEditorOpen(false)} className="rounded-full hover:bg-gray-100">
                  <X className="w-5 h-5 text-gray-500" />
                </Button>
              </div>
            </div>
            
            <div className="flex-1 flex overflow-hidden bg-gray-50">
              <div className="flex-1 flex flex-col relative">
                <Editor
                  height="100%"
                  defaultLanguage="yaml"
                  value={yamlContent}
                  onChange={handleValidate}
                  theme="vs-light"
                  options={{
                    minimap: { enabled: false },
                    fontSize: 14,
                    wordWrap: 'on',
                    scrollBeyondLastLine: false,
                    lineNumbersMinChars: 3,
                  }}
                />
                
                {yamlError && (
                  <div className="absolute bottom-4 left-4 right-4 bg-red-50 border border-red-200 text-red-700 p-3 rounded-lg shadow-lg flex items-start gap-3 animate-in slide-in-from-bottom-2">
                    <AlertCircle className="w-5 h-5 shrink-0 mt-0.5" />
                    <div>
                      <h4 className="text-sm font-bold">YAML Validation Error</h4>
                      <pre className="text-xs mt-1 whitespace-pre-wrap font-mono">{yamlError}</pre>
                    </div>
                  </div>
                )}
                
                {!yamlError && yamlContent && (
                  <div className="absolute bottom-4 left-4 bg-green-50 border border-green-200 text-green-700 px-3 py-1.5 rounded-full shadow flex items-center gap-2 text-xs font-semibold animate-in slide-in-from-bottom-2">
                    <CheckCircle2 className="w-4 h-4" />
                    Valid YAML
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}