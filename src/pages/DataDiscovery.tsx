import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Search, Filter, ShieldCheck, Database, Table as TableIcon, Info, X } from 'lucide-react';
import { api } from '../services/api';
import type { DataAsset } from '../services/api';

export function DataDiscovery() {
  const navigate = useNavigate();
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedDomains, setSelectedDomains] = useState<string[]>([]);
  const [showCertifiedOnly, setShowCertifiedOnly] = useState(false);
  const [selectedDataset, setSelectedDataset] = useState<DataAsset | null>(null);
  
  const [datasets, setDatasets] = useState<DataAsset[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let mounted = true;
    
    async function loadData() {
      setIsLoading(true);
      try {
        const data = await api.getDataAssets({ limit: 100 });
        if (mounted) {
          setDatasets(data);
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
    const matchesCertified = !showCertifiedOnly || ds.certified;

    return matchesSearch && matchesDomain && matchesCertified;
  });

  const handleRequestAccess = (dataset: DataAsset) => {
    navigate('/', { 
      state: { 
        autoQuery: `I need access to the ${dataset.catalog}.${dataset.schema_name}.${dataset.table_name} dataset.` 
      } 
    });
  };

  const newThisWeekCount = datasets.filter(ds => {
    if (!ds.created_at) return false;
    const createdDate = new Date(ds.created_at);
    const oneWeekAgo = new Date();
    oneWeekAgo.setDate(oneWeekAgo.getDate() - 7);
    return createdDate >= oneWeekAgo;
  }).length;

  return (
    <div className="space-y-6 w-full">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Data Discovery</h1>
          <p className="text-sm text-gray-500 mt-1">Discover and request access to data assets across the organization.</p>
        </div>
      </div>

      {/* High Level Info Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100 flex items-center space-x-4">
          <div className="p-3 bg-blue-50 text-blue-600 rounded-lg">
            <Database className="w-6 h-6" />
          </div>
          <div>
            <p className="text-sm font-medium text-gray-500">Total Assets</p>
            <p className="text-2xl font-bold text-gray-900">{isLoading ? '-' : datasets.length}</p>
          </div>
        </div>
        
        <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100 flex items-center space-x-4">
          <div className="p-3 bg-green-50 text-green-600 rounded-lg">
            <ShieldCheck className="w-6 h-6" />
          </div>
          <div>
            <p className="text-sm font-medium text-gray-500">Certified Datasets</p>
            <p className="text-2xl font-bold text-gray-900">{isLoading ? '-' : datasets.filter(d => d.certified).length}</p>
          </div>
        </div>

        <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100 flex items-center space-x-4">
          <div className="p-3 bg-purple-50 text-purple-600 rounded-lg">
            <TableIcon className="w-6 h-6" />
          </div>
          <div>
            <p className="text-sm font-medium text-gray-500">New This Week</p>
            <p className="text-2xl font-bold text-gray-900">{isLoading ? '-' : newThisWeekCount}</p>
          </div>
        </div>
      </div>

      {/* Table Section */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
        <div className="p-4 border-b border-gray-100 space-y-4">
          <div className="flex justify-between items-center">
            <div className="relative w-96">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
              <input
                type="text"
                placeholder="Search datasets, descriptions, owners..."
                className="w-full pl-9 pr-4 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-primary focus:border-primary"
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
              />
            </div>
            <button className="flex items-center space-x-2 px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50 transition-colors">
              <Filter className="w-4 h-4" />
              <span>Filter</span>
            </button>
          </div>

          {/* Filters (Chips) */}
          <div className="flex flex-wrap gap-2 items-center">
            <button
              onClick={() => setShowCertifiedOnly(!showCertifiedOnly)}
              className={`px-3 py-1.5 rounded-full text-sm font-semibold flex items-center space-x-1.5 transition-colors border shadow-sm ${
                showCertifiedOnly 
                  ? 'bg-green-100 text-green-800 border-green-200' 
                  : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50'
              }`}
            >
              <ShieldCheck className="w-4 h-4" />
              <span>Certified Only</span>
            </button>

            <div className="h-6 w-px bg-gray-300 mx-2" />

            {Array.from(new Set(datasets.map(ds => ds.domain).filter(Boolean))).sort().map(domain => {
              const isSelected = selectedDomains.includes(domain as string);
              return (
                <button
                  key={domain as string}
                  onClick={() => toggleDomain(domain as string)}
                  className={`px-3 py-1.5 rounded-full text-sm font-medium transition-colors border ${
                    isSelected
                      ? 'bg-primary text-white border-primary'
                      : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50'
                  }`}
                >
                  {domain}
                </button>
              );
            })}
          </div>
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
                  <td colSpan={7} className="px-6 py-8 text-center text-sm text-gray-500">
                    Loading data assets...
                  </td>
                </tr>
              ) : filteredDatasets.length === 0 ? (
                <tr>
                  <td colSpan={7} className="px-6 py-8 text-center text-sm text-gray-500">
                    No datasets found matching your search.
                  </td>
                </tr>
              ) : filteredDatasets.map((ds) => (
                <tr key={ds.id} className="hover:bg-gray-50 transition-colors">
                  <td className="px-6 py-4 whitespace-nowrap align-middle text-sm font-medium text-gray-900">{ds.domain}</td>
                  <td className="px-6 py-4 whitespace-nowrap align-middle text-sm text-gray-500">{ds.type}</td>
                  <td className="px-6 py-4 whitespace-nowrap align-middle text-sm font-medium text-gray-900 flex items-center space-x-2">
                    <span>{ds.table_name}</span>
                    {ds.certified && (
                      <ShieldCheck className="w-4 h-4 text-green-600" />
                    )}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap align-middle text-sm text-gray-500">{`${ds.catalog}.${ds.schema_name}`}</td>
                  <td className="px-6 py-4 text-sm align-middle text-gray-500 max-w-xs">
                    <div className="truncate" title={ds.description || undefined}>{ds.description}</div>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap align-middle text-left text-sm font-medium">
                    <div className="flex flex-col items-start space-y-1.5">
                      <button 
                        onClick={() => handleRequestAccess(ds)}
                        className="text-primary hover:text-primary/80 font-semibold"
                      >
                        Request Access
                      </button>
                      <button 
                        onClick={() => setSelectedDataset(ds)}
                        className="text-primary hover:text-primary/80 font-semibold"
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

      {/* Full Details Modal */}
      {selectedDataset && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-sm transition-opacity">
          <div className="bg-white rounded-xl shadow-2xl max-w-2xl w-full max-h-[90vh] overflow-hidden flex flex-col transform transition-all">
            
            {/* Header */}
            <div className="p-6 border-b border-gray-100 flex justify-between items-start bg-white">
              <div>
                <div className="flex items-center gap-3 mb-1">
                  <h2 className="text-xl font-bold text-gray-900">{selectedDataset.table_name}</h2>
                  {selectedDataset.certified && (
                    <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-green-100 text-green-800">
                      <ShieldCheck className="w-3 h-3 mr-1" /> Certified
                    </span>
                  )}
                </div>
                <p className="text-sm text-gray-500 font-mono bg-gray-50 px-2 py-1 rounded inline-block">{selectedDataset.catalog}.{selectedDataset.schema_name}</p>
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
                  {selectedDataset.description}
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
                        <span className="text-sm font-medium text-gray-900">{selectedDataset.type}</span>
                      </div>
                    </div>
                  </section>

                  <section>
                    <h3 className="text-sm font-semibold text-gray-900 mb-3 uppercase tracking-wide">Tags</h3>
                    <div className="flex flex-wrap gap-2">
                      {selectedDataset.tags.map((tag: string) => (
                        <span key={tag} className="inline-flex items-center px-2.5 py-1 rounded-md text-xs font-medium bg-blue-50 text-blue-700 border border-blue-100">
                          {tag}
                        </span>
                      ))}
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
              <button 
                onClick={() => {
                  handleRequestAccess(selectedDataset);
                  setSelectedDataset(null);
                }}
                className="px-6 py-2 bg-primary text-white rounded-lg text-sm font-medium hover:bg-primary/90 transition-colors shadow-sm"
              >
                Request Access
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
