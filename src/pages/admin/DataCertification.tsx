import { useState, useEffect } from 'react';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../../components/ui/card';
import { Search, AlertCircle, FileCheck, CheckCircle2, Plus, Edit, X, Save, History, Loader2 } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { api } from '../../services/api';
import type { DataAsset } from '../../services/api';
import type { DataContract } from '../../services/api';
import { format, parseISO } from 'date-fns';
import Editor from '@monaco-editor/react';
import yaml from 'js-yaml';

const DEFAULT_YAML = `apiVersion: v3.1.0
kind: DataContract

domain: changeme_domain
dataProduct: changeme_product_name
version: 1.0.0
status: active
id: changeme_contract_id

authoritativeDefinitions:
- type: canonical
  url: https://github.com/bitol-io/open-data-contract-standard/blob/main/docs/examples/all/full-example.odcs.yaml
  description: Canonical URL to the latest version of the contract.

description:
  purpose: "# CHANGEME: Describe the dataset purpose here."
  limitations: "# CHANGEME: Any limitations here."
  usage: "# CHANGEME: Describe how this is used."

quality:
  - id: technical_dq_threshold
    type: custom
    engine: acceldata
    description: "Technical data quality score must be 100%"
    mustBe: 100
  - id: business_dq_threshold
    type: custom
    engine: acceldata
    description: "Business logic validation score must be 100%"
    mustBe: 100
  - id: schema_drift
    type: custom
    engine: acceldata
    description: "Schema drift score must be 0%"
    mustBe: 0

servers:
  - id: production
    type: databricks
    host: prod-workspace.cloud.databricks.com
    catalog: changeme_catalog
    schema: changeme_schema

schema:
  - id: changeme_table_obj
    name: changeme_table_name
    physicalName: changeme_table_name
    physicalType: table
    businessName: "# CHANGEME: Business Name"
    description: "# CHANGEME: Provides core metrics"
    tags: [ 'changeme', 'tags' ]
    customProperties:
      - property: abac_required
        value: false
      - property: classification
        value: PII
    properties:
      - id: changeme_col_prop
        name: changeme_col
        physicalName: changeme_col
        primaryKey: true
        logicalType: string
        description: "# CHANGEME: Unique identifier"

price:
  priceAmount: 0.00
  priceCurrency: USD
  priceUnit: request

team:
  name: changeme_team_name
  members:
    - username: changeme_user
      role: Owner

roles:
  - role: data_engineer
    access: write
  - role: data_analyst
    access: read

slaProperties:
  - property: latency
    value: 1
    unit: d

customProperties:
  - property: certification_eligible
    value: true
  - property: is_mock
    value: false
  - property: databricks_tags
    value:
      "Owner group": "changeme_team"
      "Approver group": "data-governance"
      "Domain": "changeme_domain"
      "SLO/SLA": "Tier 1"
`;

export function DataCertification() {
  const [datasets, setDatasets] = useState<DataAsset[]>([]);
  const [contractsMap, setContractsMap] = useState<Record<string, DataContract>>({});
  const [isLoading, setIsLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState('');

  // Editor State
  const [isEditorOpen, setIsEditorOpen] = useState(false);
  const [editingAsset, setEditingAsset] = useState<DataAsset | null>(null);
  const [yamlContent, setYamlContent] = useState(DEFAULT_YAML);
  const [yamlError, setYamlError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [contractHistory, setContractHistory] = useState<DataContract[]>([]);
  const [showHistory, setShowHistory] = useState(false);

  const fetchHistory = async (datasetId: string) => {
    try {
      const history = await api.getContractHistory(datasetId);
      setContractHistory(history);
      if (history.length > 0) {
        setYamlContent(history[0].yaml_content);
      } else {
        setYamlContent(DEFAULT_YAML);
      }
    } catch (e) {
      console.error(e);
      setYamlContent(DEFAULT_YAML);
    }
  };

  const handleEdit = (dataset: DataAsset) => {
    setEditingAsset(dataset);
    setIsEditorOpen(true);
    setYamlError(null);
    setShowHistory(false);
    if (dataset.contract_url) {
      fetchHistory(dataset.id);
    } else {
      // It's a new contract for this dataset
      setYamlContent(
        DEFAULT_YAML
          .replace(/table_name/g, dataset.table_name)
          .replace(/main/g, dataset.catalog)
          .replace(/default/g, dataset.schema_name)
          .replace(/example_domain/g, dataset.domain || 'example_domain')
          .replace(/example_product/g, dataset.table_name)
          .replace(/example_table_obj/g, `${dataset.table_name}_obj`)
          .replace(/new_contract_id/g, crypto.randomUUID())
      );
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

  const handleCreateContract = () => {
    setEditingAsset(null);
    setIsEditorOpen(true);
    setYamlError(null);
    setShowHistory(false);
    setYamlContent(DEFAULT_YAML.replace(/new_contract_id/g, crypto.randomUUID()));
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
      setDatasets(data.filter(d => d.contract_url || d.certified || d.data_quality));
    } catch (e: any) {
      setYamlError(e.message || 'Failed to save data contract');
    } finally {
      setIsSaving(false);
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
          setDatasets(data.filter(d => d.contract_url || d.certified || d.data_quality));
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

  const filteredDatasets = datasets.filter(ds => 
    ds.table_name.toLowerCase().includes(searchTerm.toLowerCase()) || 
    `${ds.catalog}.${ds.schema_name}`.toLowerCase().includes(searchTerm.toLowerCase())
  );

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <FileCheck className="w-5 h-5 text-gray-700" />
            Data Certification
          </CardTitle>
          <CardDescription>
            Manage and review data contracts, data quality metrics (TDQ/BDQ), and certification status across the platform.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="mb-4 flex flex-col md:flex-row gap-4 items-center justify-between">
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
            <Button 
              onClick={handleCreateContract}
              className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 text-white"
            >
              <Plus className="w-4 h-4" />
              Create Contract
            </Button>
          </div>

          <div className="overflow-x-auto rounded-lg border border-gray-200">
            <table className="w-full text-sm text-left">
              <thead className="bg-gray-50 text-gray-700 font-medium border-b border-gray-200">
                <tr>
                  <th className="p-3 pl-4">Dataset</th>
                  <th className="p-3">Status</th>
                  <th className="p-3">Last Policy Run</th>
                  <th className="p-3">TDQ</th>
                  <th className="p-3">BDQ</th>
                  <th className="p-3">Freshness</th>
                  <th className="p-3">Drift</th>
                  <th className="p-3 text-right">Contract</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 bg-white">
                {isLoading ? (
                  <tr>
                    <td colSpan={8} className="p-6 text-center text-gray-500">Loading datasets...</td>
                  </tr>
                ) : filteredDatasets.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="p-6 text-center text-gray-500">No datasets found.</td>
                  </tr>
                ) : (
                  filteredDatasets.map(ds => {
                    const dq = ds.data_quality || {} as any;
                    
                    const tdq = dq.tdq !== undefined ? dq.tdq : 'N/A';
                    const bdq = dq.bdq !== undefined ? dq.bdq : 'N/A';
                    const freshness = dq.freshness || 'N/A';
                    const drift = dq.drift || 'N/A';
                    const lastRun = ds.last_synced_at ? format(parseISO(ds.last_synced_at), 'MMM d, HH:mm') : 'Unknown';

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
                            <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-blue-100 text-blue-800">
                              Pending
                            </span>
                          ) : (
                            <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-800">
                              Uncertified
                            </span>
                          )}
                        </td>
                        <td className="p-3 text-gray-600 whitespace-nowrap">{lastRun}</td>
                        <td className="p-3">
                          <span className={`font-semibold ${typeof tdq === 'number' ? (tdq >= 90 ? 'text-green-600' : 'text-orange-600') : 'text-gray-400'}`}>
                            {tdq}
                          </span>
                        </td>
                        <td className="p-3">
                          <span className={`font-semibold ${typeof bdq === 'number' ? (bdq >= 90 ? 'text-green-600' : 'text-orange-600') : 'text-gray-400'}`}>
                            {bdq}
                          </span>
                        </td>
                        <td className="p-3 text-gray-600">{freshness}</td>
                        <td className="p-3 text-gray-600">{drift}</td>
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
                <div className="w-64 border-r border-gray-200 bg-white overflow-y-auto p-4">
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
    </div>
  );
}