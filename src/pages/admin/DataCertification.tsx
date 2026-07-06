import { useState, useEffect } from 'react';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../../components/ui/card';
import { Search, AlertCircle, FileCheck, CheckCircle2, Edit, X, Save, History, Loader2, Info, ChevronUp, ChevronDown, Filter, Trash2, RefreshCw } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { api } from '../../services/api';
import type { DataContract } from '../../services/api';
import { format, parseISO } from 'date-fns';
import Editor from '@monaco-editor/react';
import yaml from 'js-yaml';

// The backend serializes naive UTC datetimes (no timezone suffix). Treat any
// such string as UTC so date-fns renders it in the viewer's local timezone.
const parseUtc = (value: string): Date =>
  parseISO(/Z|[+-]\d{2}:?\d{2}$/.test(value) ? value : `${value}Z`);

// Render a UTC timestamp in US Pacific time with an explicit tz label. Uses the
// America/Los_Angeles zone so the abbreviation auto-switches between PST and PDT
// with daylight saving rather than being hardcoded.
const formatPacific = (value: string): string =>
  parseUtc(value).toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'America/Los_Angeles',
    timeZoneName: 'short',
  });

export function DataCertification() {
  const [datasets, setDatasets] = useState<DataContract[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState('');
  const [sortField, setSortField] = useState<'name' | 'reliability' | 'lastRun' | 'created'>('name');
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('asc');
  const [statusFilter, setStatusFilter] = useState<'all' | 'certified' | 'uncertified' | 'awaiting'>('all');
  const [isSyncingContracts, setIsSyncingContracts] = useState(false);
  const [isCheckingPolicy, setIsCheckingPolicy] = useState(false);
  const [syncMessage, setSyncMessage] = useState<{ type: 'success' | 'error', text: string } | null>(null);

  // Editor State
  const [isEditorOpen, setIsEditorOpen] = useState(false);
  const [editingAsset, setEditingAsset] = useState<DataContract | null>(null);
  const [yamlContent, setYamlContent] = useState('');
  const [yamlError, setYamlError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [contractHistory, setContractHistory] = useState<DataContract[]>([]);
  const [showHistory, setShowHistory] = useState(false);
  
  // Violations Modal State
  const [violationAsset, setViolationAsset] = useState<DataContract | null>(null);

  const fetchHistory = async (datasetId: string, contractUrl?: string | null) => {
    try {
      const history = await api.getContractHistory(datasetId);
      setContractHistory(history);
      if (history.length > 0) {
        setYamlContent(history[0].yaml_content);
      } else if (contractUrl && contractUrl.startsWith('/requests/')) {
        const requestId = contractUrl.split('/').pop();
        if (requestId) {
          try {
            const request = await api.getRequest(requestId);
            if (request && request.metadata && request.metadata.odcs_yaml) {
              setYamlContent(request.metadata.odcs_yaml);
              return;
            }
          } catch (reqError) {
            console.error('Failed to fetch request for pending contract', reqError);
          }
        }
        setYamlContent('');
        setYamlError('No contract content found.');
      } else {
        setYamlContent('');
        setYamlError('No contract content found.');
      }
    } catch (e) {
      console.error(e);
      setYamlContent('');
      setYamlError('No contract content found.');
    }
  };

  const handleEdit = (dataset: DataContract) => {
    setEditingAsset(dataset);
    setIsEditorOpen(true);
    setYamlError(null);
    setShowHistory(false);
    fetchHistory(dataset.dataset_id, `/governance/certification?dataset=${dataset.dataset_id}`);
  };

  const handleDeleteContract = async (datasetId: string) => {
    if (!confirm(`Are you sure you want to delete the contract for ${datasetId}? This will remove all contract versions and unset its certified status.`)) {
      return;
    }
    
    try {
      await api.deleteDataContract(datasetId);
      
      // Reload assets to reflect changes
      const contracts = await api.getDataContracts();
      setDatasets(contracts);
    } catch (e: any) {
      console.error('Failed to delete contract', e);
      alert('Failed to delete contract: ' + e.message);
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
      let datasetId = editingAsset?.dataset_id;
      if (!datasetId) {
        const parsed = yaml.load(yamlContent) as any;
        const servers = parsed.servers || [];
        const catalog = servers[0]?.catalog || 'main';
        const schema = servers[0]?.schema || 'default';
        const schemas = parsed.schema || [];
        const physicalName = schemas[0]?.physicalName || 'unknown';
        datasetId = `${catalog}.${schema}.${physicalName}`;
      }

      await api.createDataContract(datasetId, yamlContent);
      setIsEditorOpen(false);
      // Reload assets to reflect changes
      const contracts = await api.getDataContracts();
      setDatasets(contracts);
    } catch (e: any) {
      setYamlError(e.message || 'Failed to save data contract');
    } finally {
      setIsSaving(false);
    }
  };

  const handleCheckPolicy = async (datasetId: string) => {
    setIsCheckingPolicy(true);
    setSyncMessage(null);
    try {
      const res = await api.checkPolicy(datasetId);
      setSyncMessage({ type: 'success', text: `Policy Check Started: ${res.message || 'Success'}` });
      setTimeout(() => setSyncMessage(null), 5000);
      
      // Reload assets to reflect changes
      const contracts = await api.getDataContracts();
      setDatasets(contracts);
    } catch (e: any) {
      console.error(e);
      setSyncMessage({ type: 'error', text: e.message || "Error checking policy." });
      setTimeout(() => setSyncMessage(null), 5000);
    } finally {
      setIsCheckingPolicy(false);
    }
  };

  const handleSyncContracts = async (datasetId?: string) => {
    setIsSyncingContracts(true);
    setSyncMessage(null);
    try {
      const res = await api.syncDataContracts(datasetId);
      setSyncMessage({ type: 'success', text: `Sync Complete: ${res.message}` });
      setTimeout(() => setSyncMessage(null), 5000);
      
      // Reload assets to reflect changes
      const contracts = await api.getDataContracts();
      setDatasets(contracts);
      
    } catch (e: any) {
      console.error(e);
      setSyncMessage({ type: 'error', text: e.message || "Error syncing contracts." });
      setTimeout(() => setSyncMessage(null), 5000);
    } finally {
      setIsSyncingContracts(false);
    }
  };

  useEffect(() => {
    let mounted = true;
    async function loadData() {
      try {
        const contracts = await api.getDataContracts();
        if (mounted) {
          setDatasets(contracts);
        }
      } catch (e) {
        console.error('Failed to load data assets for certification', e);
      } finally {
        if (mounted) {
          setIsLoading(false);
        }
      }
    }
    loadData();
    return () => { mounted = false; };
  }, []);

  const handleSort = (field: 'name' | 'reliability' | 'lastRun' | 'created') => {
    if (sortField === field) {
      setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortDirection(field === 'name' ? 'asc' : 'desc');
    }
  };

  const getSortIcon = (field: 'name' | 'reliability' | 'lastRun' | 'created') => {
    if (sortField !== field) return <ChevronUp className="w-3 h-3 text-gray-300 opacity-0 group-hover:opacity-100" />;
    return sortDirection === 'asc' ? <ChevronUp className="w-3 h-3 text-primary" /> : <ChevronDown className="w-3 h-3 text-primary" />;
  };

  const getStatus = (contract: DataContract) => {
    const dq = contract.data_quality || {} as any;
    const rel = dq.failed_rule_count !== undefined ? dq.failed_rule_count : (dq.reliability !== undefined ? dq.reliability : 'N/A');

    // Certified is driven solely by the Unity Catalog certification status.
    // A clean policy scan (zero violations) does NOT imply certified — that
    // was the bug where uncertified datasets showed under the "Certified" filter.
    if (contract.certified) return 'certified';

    // Legacy "Invalid" status removed: a placeholder/invalid contract simply
    // can't be certified, so it falls under uncertified.
    const isInvalid = contract.yaml_content.toLowerCase().includes('changeme');
    if (isInvalid) return 'uncertified';

    // Never scanned and no data-quality signal yet → awaiting the first run.
    const hasBeenScanned = contract.certification_violations !== null && contract.certification_violations !== undefined;
    if (!hasBeenScanned && rel === 'N/A') return 'awaiting';

    return 'uncertified';
  };

  const processedDatasets = datasets
    .filter(contract => {
      const tbName = contract.table_name || contract.dataset_id || '';
      const matchesSearch = tbName.toLowerCase().includes(searchTerm.toLowerCase()) || 
                            `${contract.catalog || ''}.${contract.schema_name || ''}`.toLowerCase().includes(searchTerm.toLowerCase());
      if (statusFilter === 'all') return matchesSearch;
      
      return matchesSearch && getStatus(contract) === statusFilter;
    })
    .sort((a, b) => {
      const dqA = a.data_quality || {} as any;
      const dqB = b.data_quality || {} as any;
      
      let valA: any = (a.table_name || a.dataset_id || '').toLowerCase();
      let valB: any = (b.table_name || b.dataset_id || '').toLowerCase();
      
      if (sortField === 'reliability') {
        valA = dqA.failed_rule_count !== undefined ? dqA.failed_rule_count : (dqA.reliability !== undefined && dqA.reliability !== 'N/A' ? Number(dqA.reliability) : -1);
        valB = dqB.failed_rule_count !== undefined ? dqB.failed_rule_count : (dqB.reliability !== undefined && dqB.reliability !== 'N/A' ? Number(dqB.reliability) : -1);
      } else if (sortField === 'lastRun') {
        valA = a.last_synced_at ? parseUtc(a.last_synced_at).getTime() : 0;
        valB = b.last_synced_at ? parseUtc(b.last_synced_at).getTime() : 0;
      } else if (sortField === 'created') {
        valA = a.created_at ? parseUtc(a.created_at).getTime() : 0;
        valB = b.created_at ? parseUtc(b.created_at).getTime() : 0;
      }
      
      if (valA < valB) return sortDirection === 'asc' ? -1 : 1;
      if (valA > valB) return sortDirection === 'asc' ? 1 : -1;
      return 0;
    });

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <FileCheck className="w-5 h-5 text-gray-700" />
            Data Certification
          </CardTitle>
          <CardDescription>
            Manage and review data contracts, data quality metrics (TDQ/BDQ), and certification status for datasets.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="mb-4 flex flex-col md:flex-row gap-4 items-center justify-between">
            <div className="flex w-full md:w-auto gap-4 flex-1">
              <div className="relative w-full md:w-96">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                <input
                  type="text"
                  placeholder="Search by table or location..."
                  className="w-full pl-9 pr-4 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-primary focus:border-primary"
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                />
              </div>
              <div className="relative shrink-0">
                <div className="flex items-center border border-gray-300 rounded-lg px-3 py-2 bg-white text-sm focus-within:ring-2 focus-within:ring-primary focus-within:border-primary">
                  <Filter className="w-4 h-4 text-gray-500 mr-2" />
                  <select 
                    value={statusFilter}
                    onChange={(e) => setStatusFilter(e.target.value as any)}
                    className="bg-transparent border-none outline-none focus:ring-0 text-gray-700 w-40"
                  >
                    <option value="all">All Statuses</option>
                    <option value="certified">Certified</option>
                    <option value="uncertified">Uncertified</option>
                    <option value="awaiting">Awaiting Scan</option>
                  </select>
                </div>
              </div>
            </div>
            <div className="flex items-center gap-3">
              <Button
                onClick={() => handleSyncContracts()}
                disabled={isSyncingContracts}
                className="flex items-center gap-2 bg-primary text-white disabled:opacity-50"
              >
                {isSyncingContracts ? <Loader2 className="w-4 h-4 animate-spin" /> : <FileCheck className="w-4 h-4" />}
                Sync Data Contracts
              </Button>
            </div>
          </div>

          {syncMessage && (
            <div className={`mb-4 p-3 rounded-lg flex items-center gap-2 ${syncMessage.type === 'success' ? 'bg-green-50 text-green-700 border border-green-200' : 'bg-red-50 text-red-700 border border-red-200'}`}>
              {syncMessage.type === 'success' ? <CheckCircle2 className="w-5 h-5" /> : <AlertCircle className="w-5 h-5" />}
              <span className="text-sm font-medium">{syncMessage.text}</span>
            </div>
          )}

          <div className="overflow-x-auto rounded-lg border border-gray-200">
            <table className="w-full text-sm text-left">
              <thead className="bg-gray-50 text-gray-700 font-medium border-b border-gray-200 select-none">
                <tr>
                  <th 
                    className="p-3 pl-4 cursor-pointer hover:bg-gray-100 group transition-colors"
                    onClick={() => handleSort('name')}
                  >
                    <div className="flex items-center justify-between">Dataset {getSortIcon('name')}</div>
                  </th>
                  <th className="p-3">Status</th>
                  <th 
                    className="p-3 cursor-pointer hover:bg-gray-100 group transition-colors"
                    onClick={() => handleSort('created')}
                  >
                    <div className="flex items-center justify-between">Created {getSortIcon('created')}</div>
                  </th>
                  <th 
                    className="p-3 cursor-pointer hover:bg-gray-100 group transition-colors"
                    onClick={() => handleSort('lastRun')}
                  >
                    <div className="flex items-center justify-between">Last Policy Run {getSortIcon('lastRun')}</div>
                  </th>
                  <th 
                    className="p-3 cursor-pointer hover:bg-gray-100 group transition-colors"
                    onClick={() => handleSort('reliability')}
                  >
                    <div className="flex items-center justify-between">Failed Rules {getSortIcon('reliability')}</div>
                  </th>
                  <th className="p-3 text-right">Contract</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 bg-white">
                {isLoading ? (
                  <tr>
                    <td colSpan={6} className="p-6 text-center text-gray-500">Loading datasets...</td>
                  </tr>
                ) : processedDatasets.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="p-6 text-center text-gray-500">No datasets found.</td>
                  </tr>
                ) : (
                  processedDatasets.map(contract => {
                    const dq = contract.data_quality || {} as any;
                    
                    const rel = dq.failed_rule_count !== undefined ? dq.failed_rule_count : (dq.reliability !== undefined ? dq.reliability : 'N/A');
                    const lastRun = contract.last_synced_at ? formatPacific(contract.last_synced_at) : 'Unknown';
                    const createdDate = contract.created_at ? format(parseUtc(contract.created_at), 'MMM d, yyyy') : 'Unknown';
                    const status = getStatus(contract);
                    const failedRules = Array.isArray(dq.failed_rules) ? dq.failed_rules : [];
                    const hasViolations = !!((contract.certification_violations && contract.certification_violations.length > 0) || failedRules.length > 0);

                    return (
                      <tr key={contract.dataset_id} className="hover:bg-gray-50 transition-colors">
                        <td className="p-3 pl-4">
                          <div className="font-medium text-gray-900">{contract.table_name || contract.dataset_id}</div>
                          <div className="text-xs text-gray-500 font-mono mt-0.5">
                            {contract.catalog || ''}{contract.schema_name ? `.${contract.schema_name}` : ''}
                          </div>
                        </td>
                        <td className="p-3">
                          {status === 'certified' ? (
                            <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-green-100 text-green-800">
                              <CheckCircle2 className="w-3 h-3 mr-1" /> Certified
                            </span>
                          ) : status === 'awaiting' ? (
                            <span 
                              className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-yellow-100 text-yellow-800"
                              title="Run Enforcement Sentinel to fetch policy violations and scores"
                            >
                              <Info className="w-3 h-3 mr-1" /> Awaiting Scan
                            </span>
                          ) : hasViolations ? (
                            <button
                              onClick={() => setViolationAsset(contract)}
                              className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-800 cursor-pointer hover:bg-gray-200 transition-colors"
                            >
                              Uncertified
                              <AlertCircle className="w-3 h-3 ml-1 text-amber-500" />
                            </button>
                          ) : (
                            <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-800">
                              Uncertified
                            </span>
                          )}
                        </td>
                        <td className="p-3 text-gray-600 whitespace-nowrap">{createdDate}</td>
                        <td className="p-3 text-gray-600 whitespace-nowrap">{lastRun}</td>
                        <td className="p-3">
                          {failedRules.length > 0 ? (
                            <button
                              onClick={() => setViolationAsset(contract)}
                              className="font-semibold text-red-600 hover:underline cursor-pointer"
                              title="View failing data quality rules"
                            >
                              {rel}
                            </button>
                          ) : (typeof rel === 'number' && rel === 0) ? (
                            <span className="font-semibold text-green-600">0</span>
                          ) : (typeof rel === 'number' && rel > 0) ? (
                            <span className="font-semibold text-red-600">{rel}</span>
                          ) : (
                            <span
                              className="text-gray-400 cursor-help"
                              title={rel === -1 || rel === '-1'
                                ? "Couldn't fetch data quality history. Check that the table has a 'reliability_window' tag, then re-run the Enforcement Sentinel."
                                : 'Run the Enforcement Sentinel to evaluate data quality.'}
                            >
                              &mdash;
                            </span>
                          )}
                        </td>
                        <td className="p-3 text-right">
                          <div className="flex items-center justify-end gap-1.5">
                            <Button 
                              variant="outline" 
                              size="sm" 
                              onClick={() => handleSyncContracts(contract.dataset_id)}
                              disabled={isSyncingContracts}
                              className="text-xs h-7 px-2 gap-1 border-green-200 text-green-600 hover:bg-green-50"
                              title="Sync Contract"
                            >
                              <RefreshCw className="w-3.5 h-3.5" />
                              Sync
                            </Button>
                            <Button 
                              variant="outline" 
                              size="sm" 
                              onClick={() => handleCheckPolicy(contract.dataset_id)}
                              disabled={isCheckingPolicy}
                              className="text-xs h-7 px-2 gap-1 border-purple-200 text-purple-600 hover:bg-purple-50"
                              title="Run Policy Check"
                            >
                              <FileCheck className="w-3.5 h-3.5" />
                              Check
                            </Button>
                            <Button 
                              variant="outline" 
                              size="sm" 
                              onClick={() => handleEdit(contract)}
                              className="text-xs h-7 px-2 gap-1 border-blue-200 text-blue-600 hover:bg-blue-50"
                              title="Edit Contract"
                            >
                              <Edit className="w-3.5 h-3.5" />
                              Edit
                            </Button>
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => handleDeleteContract(contract.dataset_id)}
                              className="text-xs h-7 px-2 gap-1 border-red-200 text-red-600 hover:bg-red-50"
                              title="Delete Contract"
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                              Delete
                            </Button>
                          </div>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      {/* Editor Modal */}
      {isEditorOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4 animate-in fade-in">
          <div className="bg-white rounded-xl shadow-xl w-full max-w-[95vw] xl:max-w-[1200px] h-[90vh] flex flex-col overflow-hidden animate-in zoom-in-95">
            <div className="flex items-center justify-between p-4 border-b border-gray-100 bg-white">
              <div>
                <h3 className="text-lg font-semibold text-gray-900 flex items-center gap-2">
                  <FileCheck className="w-5 h-5 text-blue-600" />
                  {editingAsset ? 'Edit Data Contract' : 'Create Data Contract'}
                </h3>
                {editingAsset && (
                  <p className="text-xs text-gray-500 mt-1 font-mono">{editingAsset.catalog}.{editingAsset.schema_name}.{editingAsset.table_name}</p>
                )}
              </div>
              <div className="flex items-center gap-3">
                <Button 
                  variant="outline" 
                  size="sm" 
                  onClick={() => setShowHistory(!showHistory)}
                  className="flex items-center gap-2"
                >
                  <History className="w-4 h-4" />
                  {showHistory ? 'Hide History' : 'View History'}
                </Button>
                <Button 
                  variant="default" 
                  size="sm" 
                  disabled={!!yamlError || isSaving}
                  onClick={handleSave}
                  className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 text-white"
                >
                  {isSaving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                  Save Contract
                </Button>
                <Button variant="ghost" size="sm" onClick={() => setIsEditorOpen(false)} className="rounded-full hover:bg-gray-100">
                  <X className="w-5 h-5 text-gray-500" />
                </Button>
              </div>
            </div>
            
            <div className="flex-1 flex overflow-hidden bg-gray-50">
              {showHistory && (
                <div className="w-64 shrink-0 border-r border-gray-200 bg-white overflow-y-auto p-4">
                  <h4 className="text-sm font-semibold text-gray-900 mb-4">Version History</h4>
                  <div className="space-y-3">
                    {contractHistory.map(version => (
                      <button
                        key={version.version}
                        onClick={() => {
                          setYamlContent(version.yaml_content);
                          setYamlError(null);
                        }}
                        className={`w-full text-left p-3 rounded-lg border text-sm transition-colors ${yamlContent === version.yaml_content ? 'bg-blue-50 border-blue-200 shadow-sm' : 'bg-white border-gray-100 hover:bg-gray-50'}`}
                      >
                        <div className="font-semibold text-gray-900 mb-1">v{version.version}.0</div>
                        <div className="text-xs text-gray-500">{format(parseUtc(version.created_at), 'MMM d, yyyy HH:mm')}</div>
                        {version.is_active && <span className="inline-block mt-2 px-2 py-0.5 bg-green-100 text-green-800 text-[10px] rounded-full font-bold">ACTIVE</span>}
                      </button>
                    ))}
                    {contractHistory.length === 0 && (
                      <div className="text-xs text-gray-500 text-center py-4">No prior versions.</div>
                    )}
                  </div>
                </div>
              )}
              
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
                
                {/* Error Banner */}
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

      {/* Violations Modal */}
      {violationAsset && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4 animate-in fade-in" onClick={() => setViolationAsset(null)}>
          <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl flex flex-col overflow-hidden animate-in zoom-in-95" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-gray-100 bg-white">
              <div>
                <h3 className="text-lg font-semibold text-gray-900 flex items-center gap-2">
                  <AlertCircle className="w-5 h-5 text-amber-500" />
                  Certification Issues
                </h3>
                <p className="text-xs text-gray-500 mt-1 font-mono">{violationAsset.catalog || ''}.{violationAsset.schema_name || ''}.{violationAsset.table_name || violationAsset.dataset_id}</p>
              </div>
              <Button 
                variant="ghost" 
                size="sm" 
                onClick={() => setViolationAsset(null)}
                className="w-8 h-8 p-0"
              >
                <X className="w-4 h-4" />
              </Button>
            </div>
            <div className="p-6 bg-gray-50 flex-1 overflow-y-auto max-h-[60vh]">
              {(() => {
                const failedRules = Array.isArray(violationAsset.data_quality?.failed_rules) ? violationAsset.data_quality.failed_rules : [];
                if (failedRules.length === 0) return null;
                return (
                  <div className="mb-6">
                    <p className="text-sm text-gray-600 mb-3">
                      Failing data quality rules within the reliability window:
                    </p>
                    <div className="overflow-hidden rounded-lg border border-gray-200">
                      <table className="w-full text-sm bg-white">
                        <thead className="bg-gray-50 text-gray-500 text-xs border-b border-gray-200">
                          <tr>
                            <th className="text-left font-medium p-2 pl-3">Rule</th>
                            <th className="text-left font-medium p-2">Column</th>
                            <th className="text-right font-medium p-2">Score</th>
                            <th className="text-right font-medium p-2">Threshold</th>
                            <th className="text-right font-medium p-2 pr-3">Rows Failed</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-100">
                          {failedRules.map((fr: any, i: number) => (
                            <tr key={i} className="align-top">
                              <td className="p-2 pl-3">
                                <div className="font-medium text-gray-900">{fr.rule || 'Unnamed rule'}</div>
                                <div className="text-[11px] text-gray-500">
                                  {[fr.dimension, fr.rule_type].filter(Boolean).join(' · ')}
                                </div>
                                {fr.table && <div className="text-[10px] text-gray-400 font-mono break-all">{fr.table}</div>}
                              </td>
                              <td className="p-2 text-gray-700">{fr.column || '—'}</td>
                              <td className="p-2 text-right font-semibold text-red-600">{fr.score != null ? `${Number(fr.score).toFixed(2)}%` : '—'}</td>
                              <td className="p-2 text-right text-gray-600">{fr.threshold != null ? `${Number(fr.threshold).toFixed(2)}%` : '—'}</td>
                              <td className="p-2 pr-3 text-right text-gray-600">{fr.rows_failed != null ? Number(fr.rows_failed).toLocaleString() : '—'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                );
              })()}

              {violationAsset.certification_violations && violationAsset.certification_violations.length > 0 && (
                <>
                  <p className="text-sm text-gray-600 mb-4">
                    This dataset fails the following Open Policy Agent (OPA) checks required for certification:
                  </p>
                  <ul className="space-y-3">
                    {violationAsset.certification_violations?.map((v, i) => (
                      <li key={i} className="flex items-start gap-3 bg-white p-3 rounded-lg border border-gray-200 shadow-sm">
                        <span className="flex-shrink-0 w-6 h-6 rounded-full bg-red-100 text-red-700 flex items-center justify-center text-xs font-bold mt-0.5">
                          {i + 1}
                        </span>
                        <span className="text-sm text-gray-800 leading-snug pt-0.5">
                          {v.replace(/^\d+\.\s*/, '')}
                        </span>
                      </li>
                    ))}
                  </ul>
                </>
              )}
              <div className="mt-6 p-4 bg-blue-50 text-blue-800 rounded-lg border border-blue-100 text-sm">
                <p><strong>Next Steps:</strong> Once the data engineering team resolves these issues in Databricks (e.g., by adding missing tags, defining RBAC, or improving data quality scores), the next Enforcement Sentinel run will automatically detect the changes and generate a Data Certification request.</p>
              </div>
            </div>
            <div className="p-4 border-t border-gray-100 bg-white flex justify-end">
              <Button onClick={() => setViolationAsset(null)}>Close</Button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}