import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../../components/ui/card';
import { Search, AlertCircle, FileCheck, CheckCircle2, Edit, X, Save, History, Loader2, Info, ChevronUp, ChevronDown, Filter, Plus, Trash2 } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { api } from '../../services/api';
import type { DataAsset } from '../../services/api';
import type { DataContract } from '../../services/api';
import { format, parseISO } from 'date-fns';
import Editor from '@monaco-editor/react';
import yaml from 'js-yaml';

export function DataCertification() {
  const [datasets, setDatasets] = useState<DataAsset[]>([]);
  const [contractsMap, setContractsMap] = useState<Record<string, DataContract>>({});
  const [isLoading, setIsLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState('');
  const [sortField, setSortField] = useState<'name' | 'tdq' | 'bdq' | 'lastRun' | 'discovered'>('name');
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('asc');
  const [statusFilter, setStatusFilter] = useState<'all' | 'certified' | 'uncertified' | 'pending' | 'invalid' | 'awaiting'>('all');
  const [isDrafting, setIsDrafting] = useState(false);
  const [isDraftModalOpen, setIsDraftModalOpen] = useState(false);

  // Drafting Modal Dynamic State
  const [draftRows, setDraftRows] = useState<Array<{id: number, catalog: string, schema: string, table: string}>>([{id: Date.now(), catalog: '', schema: '', table: ''}]);
  const [catalogs, setCatalogs] = useState<{name: string}[]>([]);
  const [schemasMap, setSchemasMap] = useState<Record<string, {name: string}[]>>({});
  const [tablesMap, setTablesMap] = useState<Record<string, {name: string, type: string}[]>>({});
  const [isLoadingCatalogs, setIsLoadingCatalogs] = useState(false);
  const [draftSuccessId, setDraftSuccessId] = useState<string | null>(null);

  // Editor State
  const [isEditorOpen, setIsEditorOpen] = useState(false);
  const [editingAsset, setEditingAsset] = useState<DataAsset | null>(null);
  const [yamlContent, setYamlContent] = useState('');
  const [yamlError, setYamlError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [contractHistory, setContractHistory] = useState<DataContract[]>([]);
  const [showHistory, setShowHistory] = useState(false);
  
  // Violations Modal State
  const [violationAsset, setViolationAsset] = useState<DataAsset | null>(null);

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

  const handleEdit = (dataset: DataAsset) => {
    setEditingAsset(dataset);
    setIsEditorOpen(true);
    setYamlError(null);
    setShowHistory(false);
    if (dataset.contract_url) {
      fetchHistory(dataset.id, dataset.contract_url);
    } else {
      setYamlContent('');
      setYamlError('No existing contract found to edit. Please draft a new contract using the "Add Data Contract" button.');
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
      let datasetId = editingAsset?.id;
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
      const [data, contracts] = await Promise.all([
        api.getDataAssets(),
        api.getDataContracts()
      ]);
      const map: Record<string, DataContract> = {};
      contracts.forEach(c => map[c.dataset_id] = c);
      setContractsMap(map);
      setDatasets(data.filter(d => d.contract_url || d.certified || d.data_quality || (Array.isArray(d.tags) && d.tags.includes('certification_eligible'))));
    } catch (e: any) {
      setYamlError(e.message || 'Failed to save data contract');
    } finally {
      setIsSaving(false);
    }
  };

  useEffect(() => {
    let mounted = true;
    if (isDraftModalOpen && catalogs.length === 0) {
      setIsLoadingCatalogs(true);
      api.getDatabricksCatalogs().then(cats => {
        if (mounted) setCatalogs(cats);
      }).catch(console.error).finally(() => {
        if (mounted) setIsLoadingCatalogs(false);
      });
    }
    return () => { mounted = false; };
  }, [isDraftModalOpen, catalogs.length]);

  const handleRowChange = async (id: number, field: 'catalog' | 'schema' | 'table', value: string) => {
    setDraftRows(prev => prev.map(r => r.id === id ? { ...r, [field]: value, ...(field === 'catalog' ? {schema: '', table: ''} : {}), ...(field === 'schema' ? {table: ''} : {}) } : r));
    
    if (field === 'catalog' && value && !schemasMap[value]) {
      try {
        const schemas = await api.getDatabricksSchemas(value);
        setSchemasMap(prev => ({...prev, [value]: schemas}));
      } catch(e) { console.error(e); }
    } else if (field === 'schema' && value) {
      const row = draftRows.find(r => r.id === id);
      const cat = row?.catalog || value.split('.')[0]; // somewhat heuristic
      if (cat && !tablesMap[`${cat}.${value}`]) {
        try {
          const tables = await api.getDatabricksTables(cat, value);
          setTablesMap(prev => ({...prev, [`${cat}.${value}`]: tables}));
        } catch(e) { console.error(e); }
      }
    }
  };

  const addDraftRow = () => {
    setDraftRows(prev => [...prev, { id: Date.now(), catalog: '', schema: '', table: '' }]);
  };

  const removeDraftRow = (id: number) => {
    setDraftRows(prev => prev.filter(r => r.id !== id));
  };

  const handleDraftSubmit = async () => {
    const validRows = draftRows.filter(r => r.catalog && r.schema && r.table);
    if (validRows.length === 0) return;
    
    const datasetIds = validRows.map(r => `${r.catalog}.${r.schema}.${r.table}`);
    
    try {
      setIsDrafting(true);
      const res = await api.draftDataContract(datasetIds);
      setDraftSuccessId(res.request_id);
      // Wait for user to interact with the success banner
    } catch (e) {
      console.error(e);
      alert("Error drafting contract.");
    } finally {
      setIsDrafting(false);
    }
  };

  useEffect(() => {
    let mounted = true;
    async function loadData() {
      try {
        const [data, contracts] = await Promise.all([
          api.getDataAssets(),
          api.getDataContracts()
        ]);
        if (mounted) {
          const map: Record<string, DataContract> = {};
          contracts.forEach(c => map[c.dataset_id] = c);
          setContractsMap(map);
          setDatasets(data.filter(d => map[d.id] || d.contract_url));
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

  const handleSort = (field: 'name' | 'tdq' | 'bdq' | 'lastRun' | 'discovered') => {
    if (sortField === field) {
      setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortDirection(field === 'name' ? 'asc' : 'desc');
    }
  };

  const getSortIcon = (field: 'name' | 'tdq' | 'bdq' | 'lastRun' | 'discovered') => {
    if (sortField !== field) return <ChevronUp className="w-3 h-3 text-gray-300 opacity-0 group-hover:opacity-100" />;
    return sortDirection === 'asc' ? <ChevronUp className="w-3 h-3 text-primary" /> : <ChevronDown className="w-3 h-3 text-primary" />;
  };

  const getStatus = (ds: DataAsset, contract: DataContract) => {
    const dq = ds.data_quality || {} as any;
    const tdq = dq.tdq !== undefined ? dq.tdq : 'N/A';
    const isInvalid = contract && contract.yaml_content.toLowerCase().includes('changeme');

    if (isInvalid) return 'invalid';
    if (ds.certified) return 'certified';
    if (ds.contract_url) return 'pending';
    if (tdq === 'N/A') return 'awaiting';
    return 'uncertified';
  };

  const processedDatasets = datasets
    .filter(ds => {
      const matchesSearch = ds.table_name.toLowerCase().includes(searchTerm.toLowerCase()) || 
                            `${ds.catalog}.${ds.schema_name}`.toLowerCase().includes(searchTerm.toLowerCase());
      if (statusFilter === 'all') return matchesSearch;
      
      const contract = contractsMap[ds.id];
      return matchesSearch && getStatus(ds, contract) === statusFilter;
    })
    .sort((a, b) => {
      const dqA = a.data_quality || {} as any;
      const dqB = b.data_quality || {} as any;
      
      let valA: any = a.table_name.toLowerCase();
      let valB: any = b.table_name.toLowerCase();
      
      if (sortField === 'tdq') {
        valA = dqA.tdq !== undefined && dqA.tdq !== 'N/A' ? Number(dqA.tdq) : -1;
        valB = dqB.tdq !== undefined && dqB.tdq !== 'N/A' ? Number(dqB.tdq) : -1;
      } else if (sortField === 'bdq') {
        valA = dqA.bdq !== undefined && dqA.bdq !== 'N/A' ? Number(dqA.bdq) : -1;
        valB = dqB.bdq !== undefined && dqB.bdq !== 'N/A' ? Number(dqB.bdq) : -1;
      } else if (sortField === 'lastRun') {
        valA = a.last_synced_at ? new Date(a.last_synced_at).getTime() : 0;
        valB = b.last_synced_at ? new Date(b.last_synced_at).getTime() : 0;
      } else if (sortField === 'discovered') {
        valA = a.created_at ? new Date(a.created_at).getTime() : 0;
        valB = b.created_at ? new Date(b.created_at).getTime() : 0;
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
            Manage and review data contracts, data quality metrics (TDQ/BDQ), and certification status for datasets marked as <code className="text-xs bg-gray-100 px-1 py-0.5 rounded">certification_eligible</code>.
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
                    <option value="pending">Pending Approval</option>
                    <option value="uncertified">Uncertified</option>
                    <option value="invalid">Invalid (Changeme)</option>
                    <option value="awaiting">Awaiting Scan</option>
                  </select>
                </div>
              </div>
            </div>
            <div className="flex items-center gap-3">
              <Button
                onClick={() => setIsDraftModalOpen(true)}
                className="flex items-center gap-2 bg-primary text-white"
              >
                <FileCheck className="w-4 h-4" />
                Add Data Contract
              </Button>
            </div>
          </div>

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
                    onClick={() => handleSort('discovered')}
                  >
                    <div className="flex items-center justify-between">Discovered {getSortIcon('discovered')}</div>
                  </th>
                  <th 
                    className="p-3 cursor-pointer hover:bg-gray-100 group transition-colors"
                    onClick={() => handleSort('lastRun')}
                  >
                    <div className="flex items-center justify-between">Last Policy Run {getSortIcon('lastRun')}</div>
                  </th>
                  <th 
                    className="p-3 cursor-pointer hover:bg-gray-100 group transition-colors"
                    onClick={() => handleSort('tdq')}
                  >
                    <div className="flex items-center justify-between">TDQ {getSortIcon('tdq')}</div>
                  </th>
                  <th 
                    className="p-3 cursor-pointer hover:bg-gray-100 group transition-colors"
                    onClick={() => handleSort('bdq')}
                  >
                    <div className="flex items-center justify-between">BDQ {getSortIcon('bdq')}</div>
                  </th>
                  <th className="p-3">Freshness</th>
                  <th className="p-3">Drift</th>
                  <th className="p-3 text-right">Contract</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 bg-white">
                {isLoading ? (
                  <tr>
                    <td colSpan={9} className="p-6 text-center text-gray-500">Loading datasets...</td>
                  </tr>
                ) : processedDatasets.length === 0 ? (
                  <tr>
                    <td colSpan={9} className="p-6 text-center text-gray-500">No datasets found.</td>
                  </tr>
                ) : (
                  processedDatasets.map(ds => {
                    const dq = ds.data_quality || {} as any;
                    
                    const tdq = dq.tdq !== undefined ? dq.tdq : 'N/A';
                    const bdq = dq.bdq !== undefined ? dq.bdq : 'N/A';
                    const freshness = dq.freshness || 'N/A';
                    const drift = dq.drift || 'N/A';
                    const lastRun = ds.last_synced_at ? format(parseISO(ds.last_synced_at), 'MMM d, HH:mm') : 'Unknown';
                    const discoveredDate = ds.created_at ? format(parseISO(ds.created_at), 'MMM d, yyyy') : 'Unknown';

                    const contract = contractsMap[ds.id];
                    const isInvalid = contract && contract.yaml_content.toLowerCase().includes('changeme');

                    return (
                      <tr key={ds.id} className="hover:bg-gray-50 transition-colors">
                        <td className="p-3 pl-4">
                          <div className="font-medium text-gray-900">{ds.table_name}</div>
                          <div className="text-xs text-gray-500 font-mono mt-0.5">{ds.catalog}.{ds.schema_name}</div>
                        </td>
                        <td className="p-3">
                          {isInvalid ? (
                            <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-red-100 text-red-800">
                              <AlertCircle className="w-3 h-3 mr-1" /> Invalid
                            </span>
                          ) : ds.certified ? (
                            <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-green-100 text-green-800">
                              <CheckCircle2 className="w-3 h-3 mr-1" /> Certified
                            </span>
                          ) : ds.contract_url ? (
                            <Link 
                              to={ds.contract_url.startsWith('/requests') ? '/approvals' : `/governance/certification?dataset=${ds.id}`}
                              className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-blue-100 text-blue-800 hover:bg-blue-200 transition-colors"
                            >
                              {ds.contract_url.startsWith('/requests') ? 'Pending Approval \u2192' : 'Pending'}
                            </Link>
                          ) : tdq === 'N/A' ? (
                            <span 
                              className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-yellow-100 text-yellow-800"
                              title="Run Enforcement Sentinel to fetch policy violations and scores"
                            >
                              <Info className="w-3 h-3 mr-1" /> Awaiting Scan
                            </span>
                          ) : (
                            <div className="group relative inline-block">
                              <button
                                onClick={() => ds.certification_violations && ds.certification_violations.length > 0 && setViolationAsset(ds)}
                                className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-800 ${ds.certification_violations && ds.certification_violations.length > 0 ? 'cursor-pointer hover:bg-gray-200 transition-colors' : ''}`}
                              >
                                Uncertified
                                {ds.certification_violations && ds.certification_violations.length > 0 && (
                                  <AlertCircle className="w-3 h-3 ml-1 text-amber-500" />
                                )}
                              </button>
                            </div>
                          )}
                        </td>
                        <td className="p-3 text-gray-600 whitespace-nowrap">{discoveredDate}</td>
                        <td className="p-3 text-gray-600 whitespace-nowrap">{lastRun}</td>
                        <td className="p-3">
                          <span className={`font-semibold ${typeof tdq === 'number' ? (tdq >= 90 ? 'text-green-600' : 'text-orange-600') : 'text-gray-400 cursor-help'}`} title={typeof tdq === 'number' ? '' : 'Run Enforcement Sentinel to fetch score'}>
                            {tdq}
                          </span>
                        </td>
                        <td className="p-3">
                          <span className={`font-semibold ${typeof bdq === 'number' ? (bdq >= 90 ? 'text-green-600' : 'text-orange-600') : 'text-gray-400 cursor-help'}`} title={typeof bdq === 'number' ? '' : 'Run Enforcement Sentinel to fetch score'}>
                            {bdq}
                          </span>
                        </td>
                        <td className="p-3 text-gray-600">
                          <span className={`${freshness === 'N/A' ? 'text-gray-400 cursor-help' : ''}`} title={freshness === 'N/A' ? 'Run Enforcement Sentinel to fetch' : ''}>
                            {freshness}
                          </span>
                        </td>
                        <td className="p-3 text-gray-600">
                          <span className={`${drift === 'N/A' ? 'text-gray-400 cursor-help' : ''}`} title={drift === 'N/A' ? 'Run Enforcement Sentinel to fetch' : ''}>
                            {drift}
                          </span>
                        </td>
                        <td className="p-3 text-right">
                          <Button 
                            variant="outline" 
                            size="sm" 
                            onClick={() => handleEdit(ds)}
                            className="text-xs h-7 px-2 border-blue-200 text-blue-600 hover:bg-blue-50"
                          >
                            <Edit className="w-3 h-3 mr-1" />
                            Edit Contract
                          </Button>
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
                        <div className="text-xs text-gray-500">{format(parseISO(version.created_at), 'MMM d, yyyy HH:mm')}</div>
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
                  Policy Violations
                </h3>
                <p className="text-xs text-gray-500 mt-1 font-mono">{violationAsset.catalog}.{violationAsset.schema_name}.{violationAsset.table_name}</p>
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
              <p className="text-sm text-gray-600 mb-4">
                This dataset is marked as <code className="bg-gray-200 px-1 rounded text-xs">certification_eligible</code>, but currently fails the following Open Policy Agent (OPA) checks required for certification:
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

      {/* Draft Contract Modal */}
      {isDraftModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4 animate-in fade-in" onClick={() => setIsDraftModalOpen(false)}>
          <div className="bg-white rounded-xl shadow-xl w-full max-w-4xl flex flex-col overflow-hidden animate-in zoom-in-95 max-h-[80vh]" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-gray-100 bg-white shrink-0">
              <div>
                <h3 className="text-lg font-semibold text-gray-900 flex items-center gap-2">
                  <FileCheck className="w-5 h-5 text-blue-600" />
                  Add Data Contract
                </h3>
                <p className="text-xs text-gray-500 mt-1">Select one or more datasets to include in this Data Product contract.</p>
              </div>
              <Button 
                variant="ghost" 
                size="sm" 
                onClick={() => setIsDraftModalOpen(false)}
                className="w-8 h-8 p-0"
              >
                <X className="w-4 h-4" />
              </Button>
            </div>
            
            <div className="p-6 bg-gray-50 flex-1 overflow-y-auto">
              {draftSuccessId ? (
                <div className="flex flex-col items-center justify-center py-12 text-center h-full">
                  <div className="w-16 h-16 bg-green-100 rounded-full flex items-center justify-center mb-4">
                    <CheckCircle2 className="w-8 h-8 text-green-600" />
                  </div>
                  <h4 className="text-xl font-bold text-gray-900 mb-2">Contract Drafted!</h4>
                  <p className="text-sm text-gray-600 mb-6 max-w-md">
                    The AI has successfully drafted the Open Data Contract Standard (ODCS) YAML for your selected datasets. 
                    A new certification workflow has been submitted and is now awaiting review.
                  </p>
                  <div className="flex gap-3">
                    <Button variant="outline" onClick={() => {
                      setDraftSuccessId(null);
                      setDraftRows([{ id: Date.now(), catalog: '', schema: '', table: '' }]);
                    }}>
                      Draft Another
                    </Button>
                    <Link to={`/approvals`}>
                      <Button className="bg-primary text-white">
                        Review Pending Contract
                      </Button>
                    </Link>
                  </div>
                </div>
              ) : isLoadingCatalogs ? (
                <div className="flex items-center justify-center py-12 text-gray-500">
                  <Loader2 className="w-6 h-6 animate-spin mr-2" />
                  Loading Databricks Catalogs...
                </div>
              ) : (
                <div className="space-y-4">
                  {draftRows.map((row, index) => {
                    const availableSchemas = schemasMap[row.catalog] || [];
                    const availableTables = tablesMap[`${row.catalog}.${row.schema}`] || [];
                    
                    return (
                      <div key={row.id} className="flex flex-col sm:flex-row items-start sm:items-center gap-3 bg-white p-3 rounded-lg border border-gray-200 shadow-sm relative">
                        <div className="flex items-center justify-center w-6 h-6 rounded-full bg-blue-100 text-blue-700 text-xs font-bold shrink-0">
                          {index + 1}
                        </div>
                        
                        <div className="flex-1 grid grid-cols-1 sm:grid-cols-3 gap-3 w-full">
                          <div>
                            <select
                              value={row.catalog}
                              onChange={(e) => handleRowChange(row.id, 'catalog', e.target.value)}
                              className="w-full text-sm border-gray-300 rounded-md shadow-sm focus:border-primary focus:ring-primary"
                            >
                              <option value="">Select Catalog...</option>
                              {catalogs.map(c => (
                                <option key={c.name} value={c.name}>{c.name}</option>
                              ))}
                            </select>
                          </div>
                          
                          <div>
                            <select
                              value={row.schema}
                              onChange={(e) => handleRowChange(row.id, 'schema', e.target.value)}
                              disabled={!row.catalog}
                              className="w-full text-sm border-gray-300 rounded-md shadow-sm focus:border-primary focus:ring-primary disabled:bg-gray-100"
                            >
                              <option value="">Select Schema...</option>
                              {availableSchemas.map(s => (
                                <option key={s.name} value={s.name}>{s.name}</option>
                              ))}
                            </select>
                          </div>
                          
                          <div>
                            <select
                              value={row.table}
                              onChange={(e) => handleRowChange(row.id, 'table', e.target.value)}
                              disabled={!row.schema}
                              className="w-full text-sm border-gray-300 rounded-md shadow-sm focus:border-primary focus:ring-primary disabled:bg-gray-100"
                            >
                              <option value="">Select Table/View...</option>
                              {availableTables.map(t => (
                                <option key={t.name} value={t.name}>{t.name} {t.type === 'VIEW' ? '(View)' : ''}</option>
                              ))}
                            </select>
                          </div>
                        </div>

                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => removeDraftRow(row.id)}
                          disabled={draftRows.length === 1}
                          className="text-red-500 hover:text-red-700 hover:bg-red-50 shrink-0 h-9 w-9 p-0"
                          title="Remove dataset"
                        >
                          <Trash2 className="w-4 h-4" />
                        </Button>
                      </div>
                    );
                  })}
                  
                  <div className="pt-2">
                    <Button 
                      variant="outline" 
                      size="sm"
                      onClick={addDraftRow}
                      className="flex items-center gap-2 text-blue-600 border-blue-200 hover:bg-blue-50"
                    >
                      <Plus className="w-4 h-4" />
                      Add another table or view
                    </Button>
                  </div>
                </div>
              )}
            </div>

            <div className="p-4 border-t border-gray-100 bg-white flex justify-between items-center shrink-0">
              {draftSuccessId ? (
                <>
                  <div className="text-sm text-green-600 font-medium flex items-center gap-1">
                    <CheckCircle2 className="w-4 h-4" /> Successfully submitted Request {draftSuccessId.substring(0, 12)}...
                  </div>
                  <Button variant="outline" onClick={() => {
                    setIsDraftModalOpen(false);
                    setDraftSuccessId(null);
                    setDraftRows([{ id: Date.now(), catalog: '', schema: '', table: '' }]);
                    window.location.reload();
                  }}>Close</Button>
                </>
              ) : (
                <>
                  <div className="text-sm text-gray-500">
                    {draftRows.filter(r => r.catalog && r.schema && r.table).length} valid dataset(s) selected
                  </div>
                  <div className="flex gap-2">
                    <Button variant="outline" onClick={() => setIsDraftModalOpen(false)}>Cancel</Button>
                    <Button 
                      onClick={handleDraftSubmit}
                      disabled={draftRows.filter(r => r.catalog && r.schema && r.table).length === 0 || isDrafting || isLoadingCatalogs}
                      className="bg-primary text-white flex items-center gap-2"
                    >
                      {isDrafting ? <Loader2 className="w-4 h-4 animate-spin" /> : <FileCheck className="w-4 h-4" />}
                      Generate Contract via AI
                    </Button>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}