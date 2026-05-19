import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { 
  Search, ShieldCheck, Database, Table as TableIcon, Info, X,
  AlertTriangle, Factory, Car, Server, TrendingUp, Heart, Users, Box,
  LayoutDashboard, PlaySquare, ChevronRight
} from 'lucide-react';
import { api } from '../services/api';
import type { DataAsset } from '../services/api';

const getDomainIcon = (domain: string) => {
  const d = domain.toLowerCase();
  if (d.includes('risk')) return AlertTriangle;
  if (d.includes('manufacturing') || d.includes('energy')) return Factory;
  if (d.includes('vehicle') || d.includes('auto')) return Car;
  if (d.includes('it') || d.includes('tech')) return Server;
  if (d.includes('sales') || d.includes('finance')) return TrendingUp;
  if (d.includes('health')) return Heart;
  if (d.includes('customer')) return Users;
  if (d.includes('onedata') || d.includes('data')) return Database;
  return Box;
};

import yaml from 'js-yaml';

export function DataDiscovery() {
  const navigate = useNavigate();
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedDomains, setSelectedDomains] = useState<string[]>([]);
  const [selectedType, setSelectedType] = useState<string>('all');
  const [showCertifiedOnly, setShowCertifiedOnly] = useState(false);
  const [selectedDataset, setSelectedDataset] = useState<DataAsset | null>(null);
  
  const [datasets, setDatasets] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let mounted = true;
    
    async function loadData() {
      setIsLoading(true);
      try {
        const [data, dashboards, jobs, apps, genieSpaces, odpsList] = await Promise.all([
          api.getDataAssets(),
          api.getDatabricksDashboards().catch(() => []),
          api.getDatabricksJobs().catch(() => []),
          api.getDatabricksApps().catch(() => []),
          api.getDatabricksGenieSpaces().catch(() => []),
          api.getOdpsList().catch(() => [])
        ]);
        
        if (mounted) {
          // Map DATA_PRODUCT from data_assets to 'dataset'
          const mappedData = data.map(d => {
            if (d.type === 'DATA_PRODUCT') {
              return { ...d, type: 'dataset' };
            }
            return d;
          });

          // Combine all assets into a single list
          const combined = [
            ...mappedData,
            ...dashboards.map(d => ({
              id: d.id,
              table_name: d.name,
              type: 'dashboard',
              domain: 'Analytics',
              description: 'Lakeview Dashboard',
              catalog: 'workspace',
              schema_name: 'dashboards',
              certified: false,
              tags: []
            })),
            ...jobs.map(j => ({
              id: j.id,
              table_name: j.name,
              type: 'job',
              domain: 'Engineering',
              description: `Job created by ${j.creator}`,
              catalog: 'workspace',
              schema_name: 'jobs',
              certified: false,
              tags: []
            })),
            ...apps.map(a => ({
              id: a.id,
              table_name: a.name,
              type: 'app',
              domain: 'Engineering',
              description: `App created by ${a.creator}`,
              catalog: 'workspace',
              schema_name: 'apps',
              certified: false,
              tags: []
            })),
            ...genieSpaces.map(g => ({
              id: g.id,
              table_name: g.name,
              type: 'genie_space',
              domain: 'Analytics',
              description: g.description || 'Genie Space',
              catalog: 'workspace',
              schema_name: 'genie',
              certified: false,
              tags: []
            })),
            ...odpsList.map(o => ({
              id: o.id,
              table_name: o.name,
              type: 'data_product',
              domain: 'Analytics',
              description: 'ODPS Data Product',
              catalog: 'workspace',
              schema_name: 'odps',
              certified: false,
              tags: []
            }))
          ];
          setDatasets(combined);
        }
      } catch (e) {
        console.error('Failed to load data assets', e);
      } finally {
        if (mounted) {
          setIsLoading(false);
        }
      }
    }
    
    loadData();
    
    return () => {
      mounted = false;
    };
  }, []);

  const toggleDomain = (domain: string) => {
    setSelectedDomains(prev => 
      prev.includes(domain) ? prev.filter(d => d !== domain) : [...prev, domain]
    );
  };

  const filteredDatasets = datasets.filter(ds => {
    const matchesSearch = 
      ds.table_name.toLowerCase().includes(searchTerm.toLowerCase()) || 
      (ds.description && ds.description.toLowerCase().includes(searchTerm.toLowerCase())) ||
      (ds.owner && ds.owner.toLowerCase().includes(searchTerm.toLowerCase()));
    
    const matchesDomain = selectedDomains.length === 0 || (ds.domain && selectedDomains.includes(ds.domain));
    
    let matchesType = true;
    if (selectedType !== 'all') {
      const dsType = ds.type ? ds.type.toLowerCase() : '';
      if (selectedType === 'table') {
        matchesType = dsType === 'managed' || dsType === 'external' || dsType === 'view';
      } else {
        matchesType = dsType === selectedType;
      }
    }

    const matchesCertified = !showCertifiedOnly || ds.certified;

    return matchesSearch && matchesDomain && matchesType && matchesCertified;
  });

  const handleRequestAccess = async (dataset: DataAsset) => {
    if (dataset.type === 'dataset') {
      try {
        // Fetch the data contract to get the list of tables/views
        const contracts = await api.getContractHistory(dataset.id);
        if (contracts && contracts.length > 0) {
          const activeContract = contracts.find(c => c.is_active) || contracts[0];
          const parsed = yaml.load(activeContract.yaml_content) as any;
          
          if (parsed && parsed.schema && parsed.servers && parsed.servers.length > 0) {
            const server = parsed.servers[0];
            const assetsList = parsed.schema.map((s: any) => `${server.catalog}.${server.schema}.${s.physicalName || s.name}`);
            
            navigate('/', { 
              state: { 
                autoQuery: `I need access to the following assets in the ${dataset.table_name} dataset:\n${assetsList.join('\n')}` 
              } 
            });
            return;
          }
        }
      } catch (error) {
        console.error("Failed to parse data contract for dataset:", error);
      }
      
      // Fallback if we couldn't parse the contract
      navigate('/', { 
        state: { 
          autoQuery: `I need access to all tables and views in the ${dataset.table_name} dataset.` 
        } 
      });
    } else {
      navigate('/', { 
        state: { 
          autoQuery: `I need access to the ${dataset.catalog}.${dataset.schema_name}.${dataset.table_name} ${dataset.type}.` 
        } 
      });
    }
  };

  const canRequestAccess = (type: string) => {
    if (!type) return false;
    const t = type.toLowerCase();
    return t === 'managed' || t === 'external' || t === 'view' || t === 'dataset';
  };

  const uniqueDomains = Array.from(new Set(datasets.map(ds => ds.domain).filter(Boolean))).sort();
  
  const domainCounts = uniqueDomains.map(domain => ({
    name: domain,
    count: datasets.filter(ds => ds.domain === domain).length
  }));

  // Featured assets
  const featuredAssets = datasets.filter(ds => ds.certified).slice(0, 4);

  // Determine if we should show the landing view or the search results view
  const showResults = searchTerm !== '' || selectedDomains.length > 0 || selectedType !== 'all' || showCertifiedOnly;

  return (
    <div className="space-y-8 w-full max-w-7xl mx-auto pb-12">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold text-gray-900">Discover</h1>
      </div>

      {/* Big Search Bar */}
      <div className="relative group">
        <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400 group-focus-within:text-primary transition-colors" />
        <input
          type="text"
          placeholder="Search across assets..."
          className="w-full pl-12 pr-4 py-4 bg-white border border-gray-200 rounded-xl text-base shadow-sm focus:ring-2 focus:ring-primary focus:border-primary transition-all outline-none"
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
        />
      </div>

      {/* Filter Pills */}
      <div className="flex flex-wrap gap-2 items-center">
        {/* All & Certified Section */}
        <button 
          onClick={() => setSelectedType('all')}
          className={`px-4 py-2 rounded-full text-sm font-medium flex items-center gap-2 transition-colors border ${
            selectedType === 'all'
              ? 'bg-blue-50 text-blue-800 border-blue-200 shadow-sm'
              : 'bg-white text-gray-700 border-gray-200 hover:bg-gray-50'
          }`}
        >
          All
        </button>
        
        <button
          onClick={() => setShowCertifiedOnly(!showCertifiedOnly)}
          className={`px-4 py-2 rounded-full text-sm font-medium flex items-center gap-2 transition-colors border ${
            showCertifiedOnly 
              ? 'bg-green-50 text-green-800 border-green-200 shadow-sm' 
              : 'bg-white text-gray-700 border-gray-200 hover:bg-gray-50'
          }`}
        >
          <ShieldCheck className="w-4 h-4" />
          Certified
        </button>

        <div className="h-6 w-px bg-gray-300 mx-1" />

        {/* Type filters */}
        <button 
          onClick={() => setSelectedType('table')}
          className={`px-4 py-2 rounded-full text-sm font-medium flex items-center gap-2 transition-colors border ${
            selectedType === 'table'
              ? 'bg-blue-50 text-blue-800 border-blue-200 shadow-sm'
              : 'bg-white text-gray-700 border-gray-200 hover:bg-gray-50'
          }`}
        >
          <TableIcon className="w-4 h-4" /> Tables & Views
        </button>
        <button 
          onClick={() => setSelectedType('dataset')}
          className={`px-4 py-2 rounded-full text-sm font-medium flex items-center gap-2 transition-colors border ${
            selectedType === 'dataset'
              ? 'bg-blue-50 text-blue-800 border-blue-200 shadow-sm'
              : 'bg-white text-gray-700 border-gray-200 hover:bg-gray-50'
          }`}
        >
          <Database className="w-4 h-4" /> Datasets
        </button>
        <button 
          onClick={() => setSelectedType('data_product')}
          className={`px-4 py-2 rounded-full text-sm font-medium flex items-center gap-2 transition-colors border ${
            selectedType === 'data_product'
              ? 'bg-blue-50 text-blue-800 border-blue-200 shadow-sm'
              : 'bg-white text-gray-700 border-gray-200 hover:bg-gray-50'
          }`}
        >
          <Box className="w-4 h-4" /> Data Products
        </button>
        <button 
          onClick={() => setSelectedType('dashboard')}
          className={`px-4 py-2 rounded-full text-sm font-medium flex items-center gap-2 transition-colors border ${
            selectedType === 'dashboard'
              ? 'bg-blue-50 text-blue-800 border-blue-200 shadow-sm'
              : 'bg-white text-gray-700 border-gray-200 hover:bg-gray-50'
          }`}
        >
          <LayoutDashboard className="w-4 h-4" /> Dashboards
        </button>
        <button 
          onClick={() => setSelectedType('job')}
          className={`px-4 py-2 rounded-full text-sm font-medium flex items-center gap-2 transition-colors border ${
            selectedType === 'job'
              ? 'bg-blue-50 text-blue-800 border-blue-200 shadow-sm'
              : 'bg-white text-gray-700 border-gray-200 hover:bg-gray-50'
          }`}
        >
          <PlaySquare className="w-4 h-4" /> Jobs
        </button>
        <button 
          onClick={() => setSelectedType('app')}
          className={`px-4 py-2 rounded-full text-sm font-medium flex items-center gap-2 transition-colors border ${
            selectedType === 'app'
              ? 'bg-blue-50 text-blue-800 border-blue-200 shadow-sm'
              : 'bg-white text-gray-700 border-gray-200 hover:bg-gray-50'
          }`}
        >
          <Server className="w-4 h-4" /> Apps
        </button>
        <button 
          onClick={() => setSelectedType('genie_space')}
          className={`px-4 py-2 rounded-full text-sm font-medium flex items-center gap-2 transition-colors border ${
            selectedType === 'genie_space'
              ? 'bg-blue-50 text-blue-800 border-blue-200 shadow-sm'
              : 'bg-white text-gray-700 border-gray-200 hover:bg-gray-50'
          }`}
        >
          <Users className="w-4 h-4" /> Genie Spaces
        </button>
      </div>

      {!showResults ? (
        <div className="space-y-10 animate-in fade-in duration-500">
          {/* Browse by domain */}
          <section className="space-y-4">
            <div className="flex justify-between items-end">
              <div>
                <h2 className="text-xl font-bold text-gray-900">Browse by domain</h2>
                <p className="text-sm text-gray-500 mt-1">Explore data and insights organized by business area.</p>
              </div>
              <button className="text-sm text-primary hover:text-primary/80 font-medium flex items-center group">
                View all <ChevronRight className="w-4 h-4 ml-0.5 group-hover:translate-x-0.5 transition-transform" />
              </button>
            </div>
            
            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
              {domainCounts.map(domain => {
                const Icon = getDomainIcon(domain.name as string);
                return (
                  <div 
                    key={domain.name}
                    onClick={() => toggleDomain(domain.name as string)}
                    className="bg-white p-5 rounded-xl border border-gray-200 hover:border-primary hover:shadow-md cursor-pointer transition-all group flex flex-col"
                  >
                    <div className="flex items-start justify-between mb-4">
                      <div className="p-2.5 bg-blue-50/50 rounded-lg group-hover:bg-primary/10 transition-colors">
                        <Icon className="w-5 h-5 text-blue-600 group-hover:text-primary" />
                      </div>
                    </div>
                    <h3 className="font-semibold text-gray-900 mb-1 group-hover:text-primary transition-colors">{domain.name}</h3>
                    <p className="text-sm text-gray-500">{domain.count} assets</p>
                  </div>
                );
              })}
            </div>
          </section>

          {/* Featured Assets */}
          {featuredAssets.length > 0 && (
            <section className="space-y-4">
              <div className="flex justify-between items-end">
                <h2 className="text-xl font-bold text-gray-900">Featured Datasets</h2>
                <button className="text-sm text-primary hover:text-primary/80 font-medium flex items-center group">
                  View all <ChevronRight className="w-4 h-4 ml-0.5 group-hover:translate-x-0.5 transition-transform" />
                </button>
              </div>
              
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                {featuredAssets.map(asset => (
                  <div 
                    key={asset.id}
                    onClick={() => setSelectedDataset(asset)}
                    className="bg-white rounded-xl border border-gray-200 hover:border-primary hover:shadow-md cursor-pointer transition-all overflow-hidden flex flex-col group"
                  >
                    <div className="h-32 bg-gradient-to-br from-gray-50 to-gray-100 border-b border-gray-100 flex items-center justify-center p-4 relative overflow-hidden">
                      <div className="absolute inset-0 bg-grid-gray-900/[0.04] bg-[size:16px_16px]" />
                      <TableIcon className="w-12 h-12 text-gray-300 group-hover:scale-110 group-hover:text-primary/20 transition-transform duration-500 relative z-10" />
                    </div>
                    <div className="p-4 flex-1 flex flex-col">
                      <div className="flex items-center gap-2 mb-2">
                        <h3 className="font-semibold text-gray-900 truncate group-hover:text-primary transition-colors" title={asset.table_name}>{asset.table_name}</h3>
                        {asset.certified && <ShieldCheck className="w-4 h-4 text-green-600 shrink-0" />}
                      </div>
                      <p className="text-xs text-gray-500 mb-4 line-clamp-2 flex-1 leading-relaxed">{asset.description}</p>
                      <div className="flex items-center justify-between text-xs text-gray-400 mt-auto pt-3 border-t border-gray-100">
                        <span className="truncate font-medium text-gray-500">{asset.domain}</span>
                        <span className="bg-gray-100 px-2 py-0.5 rounded text-gray-600">{asset.type}</span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}
        </div>
      ) : (
        /* Table Section (Search Results) */
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden animate-in fade-in slide-in-from-bottom-4 duration-300">
          <div className="p-4 border-b border-gray-100 bg-gray-50/50 flex justify-between items-center">
            <h3 className="font-semibold text-gray-700">
              {filteredDatasets.length} {filteredDatasets.length === 1 ? 'result' : 'results'} found
            </h3>
            <button 
              onClick={() => {
                setSearchTerm('');
                setSelectedDomains([]);
                setSelectedType('all');
                setShowCertifiedOnly(false);
              }}
              className="text-sm text-gray-500 hover:text-gray-900 flex items-center gap-1"
            >
              <X className="w-4 h-4" /> Clear all filters
            </button>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="bg-gray-50 border-b border-gray-200">
                  <th className="px-6 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider">Domain</th>
                  <th className="px-6 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider">Type</th>
                  <th className="px-6 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider">Table</th>
                  <th className="px-6 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider">Location</th>
                  <th className="px-6 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider">Description</th>
                  <th className="px-6 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider text-left">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {isLoading ? (
                  <tr>
                    <td colSpan={7} className="px-6 py-12 text-center text-sm text-gray-500">
                      <div className="flex flex-col items-center justify-center space-y-3">
                        <div className="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin" />
                        <p>Loading data assets...</p>
                      </div>
                    </td>
                  </tr>
                ) : filteredDatasets.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="px-6 py-12 text-center text-sm text-gray-500">
                      No datasets found matching your criteria.
                    </td>
                  </tr>
                ) : filteredDatasets.map((ds) => (
                  <tr key={ds.id} className="hover:bg-gray-50 transition-colors group">
                    <td className="px-6 py-4 whitespace-nowrap align-middle text-sm font-medium text-gray-900">{ds.domain}</td>
                    <td className="px-6 py-4 whitespace-nowrap align-middle text-sm text-gray-500">
                      <span className="bg-gray-100 px-2 py-1 rounded text-xs">{ds.type}</span>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap align-middle text-sm font-medium text-gray-900">
                      <div className="flex items-center space-x-2">
                        <span className="group-hover:text-primary transition-colors cursor-pointer" onClick={() => setSelectedDataset(ds)}>{ds.table_name}</span>
                        {ds.certified && (
                          <div title="Certified">
                            <ShieldCheck className="w-4 h-4 text-green-600" />
                          </div>
                        )}
                      </div>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap align-middle text-sm text-gray-500 font-mono text-xs">{`${ds.catalog}.${ds.schema_name}`}</td>
                    <td className="px-6 py-4 text-sm align-middle text-gray-500 max-w-xs">
                      <div className="truncate" title={ds.description || undefined}>{ds.description}</div>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap align-middle text-left text-sm font-medium">
                      <div className="flex flex-col items-start space-y-1.5">
                        {canRequestAccess(ds.type) && (
                          <button 
                            onClick={() => handleRequestAccess(ds)}
                            className="text-primary hover:text-primary/80 font-semibold transition-colors"
                          >
                            Request Access
                          </button>
                        )}
                        <button 
                          onClick={() => setSelectedDataset(ds)}
                          className="text-gray-500 hover:text-gray-900 font-medium transition-colors"
                        >
                          View Details
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Full Details Modal */}
      {selectedDataset && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-sm transition-opacity animate-in fade-in duration-200">
          <div className="bg-white rounded-xl shadow-2xl max-w-2xl w-full max-h-[90vh] overflow-hidden flex flex-col transform transition-all animate-in zoom-in-95 duration-200">
            
            {/* Header */}
            <div className="p-6 border-b border-gray-100 flex justify-between items-start bg-white">
              <div>
                <div className="flex items-center gap-3 mb-1">
                  <h2 className="text-xl font-bold text-gray-900">{selectedDataset.table_name}</h2>
                  {selectedDataset.certified && (
                    <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-green-100 text-green-800 border border-green-200">
                      <ShieldCheck className="w-3 h-3 mr-1" /> Certified
                    </span>
                  )}
                </div>
                <p className="text-sm text-gray-500 font-mono bg-gray-50 px-2 py-1 rounded inline-block border border-gray-100">{selectedDataset.catalog}.{selectedDataset.schema_name}</p>
              </div>
              <button 
                onClick={() => setSelectedDataset(null)}
                className="p-2 rounded-lg hover:bg-gray-100 text-gray-400 hover:text-gray-600 transition-colors focus:outline-none focus:ring-2 focus:ring-primary/50"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            
            {/* Scrollable Content */}
            <div className="p-6 overflow-y-auto flex-1 space-y-8">
              
              {/* Description */}
              <section>
                <h3 className="text-sm font-semibold text-gray-900 mb-2 uppercase tracking-wide">Description</h3>
                <p className="text-sm text-gray-600 leading-relaxed bg-gray-50 p-4 rounded-lg border border-gray-100">
                  {selectedDataset.description || 'No description provided.'}
                </p>
              </section>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
                {/* Meta Info */}
                <div className="space-y-6">
                  <section>
                    <h3 className="text-sm font-semibold text-gray-900 mb-2 uppercase tracking-wide">Governance</h3>
                    <div className="space-y-3">
                      <div className="flex justify-between items-center py-2 border-b border-gray-50">
                        <span className="text-sm text-gray-500">Domain</span>
                        <span className="text-sm font-medium text-gray-900">{selectedDataset.domain}</span>
                      </div>
                      <div className="flex justify-between items-center py-2 border-b border-gray-50">
                        <span className="text-sm text-gray-500">Data Owner</span>
                        <span className="text-sm font-medium text-gray-900">{selectedDataset.owner}</span>
                      </div>
                      <div className="flex justify-between items-center py-2">
                        <span className="text-sm text-gray-500">Type</span>
                        <span className="text-sm font-medium text-gray-900 bg-gray-100 px-2 py-0.5 rounded">{selectedDataset.type}</span>
                      </div>
                    </div>
                  </section>

                  <section>
                    <h3 className="text-sm font-semibold text-gray-900 mb-3 uppercase tracking-wide">Tags</h3>
                    <div className="flex flex-wrap gap-2">
                      {selectedDataset.tags && selectedDataset.tags.length > 0 ? (
                        selectedDataset.tags.map((tag: string) => (
                          <span key={tag} className="inline-flex items-center px-2.5 py-1 rounded-md text-xs font-medium bg-blue-50 text-blue-700 border border-blue-100">
                            {tag}
                          </span>
                        ))
                      ) : (
                        <span className="text-sm text-gray-400 italic">No tags</span>
                      )}
                    </div>
                  </section>
                </div>

                {/* Quality & SLA */}
                <div className="space-y-6">
                  <section>
                    <h3 className="text-sm font-semibold text-gray-900 mb-2 uppercase tracking-wide">Service Level Agreement</h3>
                    <p className="text-sm text-gray-700 bg-amber-50 text-amber-900 border border-amber-100 p-3 rounded-lg flex items-center">
                      <Info className="w-4 h-4 mr-2 shrink-0 text-amber-600" />
                      {selectedDataset.sla || 'Not defined'}
                    </p>
                  </section>

                  <section>
                    <h3 className="text-sm font-semibold text-gray-900 mb-2 uppercase tracking-wide">Data Quality</h3>
                    {selectedDataset.data_quality ? (
                      <div className="bg-white border border-gray-200 rounded-lg overflow-hidden shadow-sm">
                        <div className="flex justify-between text-sm p-3 border-b border-gray-100">
                          <span className="text-gray-500">Freshness</span>
                          <span className="font-medium text-gray-900">{selectedDataset.data_quality.freshness}</span>
                        </div>
                        <div className="flex justify-between text-sm p-3 border-b border-gray-100">
                          <span className="text-gray-500">Completeness</span>
                          <span className="font-medium text-gray-900">{selectedDataset.data_quality.completeness}</span>
                        </div>
                        <div className="flex justify-between text-sm p-3 bg-gray-50">
                          <span className="text-gray-500">Accuracy</span>
                          <span className="font-medium text-gray-900">{selectedDataset.data_quality.accuracy}</span>
                        </div>
                      </div>
                    ) : (
                      <div className="bg-gray-50 border border-gray-200 border-dashed rounded-lg p-4 text-center">
                        <p className="text-sm text-gray-500">No quality metrics available</p>
                      </div>
                    )}
                  </section>
                </div>
              </div>
            </div>

            {/* Footer */}
            <div className="p-4 border-t border-gray-100 bg-gray-50 flex justify-end gap-3 shrink-0">
              <button
                onClick={() => setSelectedDataset(null)}
                className="px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-100 transition-colors"
              >
                Close
              </button>
              {selectedDataset.certified && (
                <a 
                  href={selectedDataset.contract_url || undefined} 
                  target="_blank" 
                  rel="noopener noreferrer"
                  className="px-4 py-2 border border-gray-300 bg-white rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50 transition-colors"
                >
                  View Contract
                </a>
              )}
              {canRequestAccess(selectedDataset.type) && (
                <button 
                  onClick={() => {
                    handleRequestAccess(selectedDataset);
                    setSelectedDataset(null);
                  }}
                  className="px-6 py-2 bg-primary text-white rounded-lg text-sm font-medium hover:bg-primary/90 transition-colors shadow-sm"
                >
                  Request Access
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
