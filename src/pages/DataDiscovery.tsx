import { useState, useEffect, useMemo, useRef, type ReactNode } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { 
  Search, ShieldCheck, Database, Table as TableIcon, Info, X,
  AlertTriangle, Factory, Car, Server, TrendingUp, Heart, Users, Box,
  Tag, FileText, Loader2,
  BookOpen, Calendar, GitBranch, AlertCircle, ChevronDown, ChevronUp,
  Link as LinkIcon, ArrowDownToLine, ArrowUpFromLine, Lock, Columns3,
  Key, ExternalLink, Network, Activity, Sparkles,
  UserCheck
} from 'lucide-react';
import { api } from '../services/api';
import type { DataAsset, TableDetailsResponse } from '../services/api';
import { useBrandingStore } from '../stores/brandingStore';
import {
  assetWorkspaceUrl,
  catalogExplorerUrl,
  workspaceLinkLabel,
} from '../lib/databricksLinks';
import { LineageGraph, type LineageSeedTable } from '../components/discover/LineageGraph';
import {
  ASSET_TYPE_ORDER,
  ASSET_TYPES,
  AssetTaxonomyExplainer,
  AssetTypeBadge,
  normalizeAssetType,
  type AssetTypeId,
} from '../lib/assetTypes';
import { ChatView, type ChatViewHandle } from '../components/chat/ChatView';
import { CatalogRails } from '../components/discover/CatalogRails';
import { useDiscoveryCatalog, useAccessibleAssets } from '../lib/catalogCache';

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

const INLINE_AGENT_STORAGE_KEY = 'discover_inline_agent';

export function DataDiscovery() {
  const navigate = useNavigate();
  const location = useLocation();
  const databricksWorkspaceUrl = useBrandingStore((s) => s.databricksWorkspaceUrl);
  // The search box now does two jobs at once: it always live-filters the
  // catalog as the user types, AND it can hand the query to the agent
  // (inline, without leaving the page) on submit.
  const [searchTerm, setSearchTerm] = useState('');
  const effectiveSearchTerm = searchTerm;

  // Inline agent panel. `agentQuery` holds the question that was sent; the
  // panel mounts a <ChatView> and we forward the query via its ref.
  const [agentQuery, setAgentQuery] = useState<string | null>(null);
  const chatRef = useRef<ChatViewHandle | null>(null);

  // Domain selector — a chip-style dropdown in the filter bar.
  const [showDomainMenu, setShowDomainMenu] = useState(false);
  const domainRef = useRef<HTMLDivElement | null>(null);

  const [selectedDomains, setSelectedDomains] = useState<string[]>([]);
  const [selectedType, setSelectedType] = useState<AssetTypeId | 'all'>('all');
  const [showCertifiedOnly, setShowCertifiedOnly] = useState(false);
  const [showAccessibleOnly, setShowAccessibleOnly] = useState(false);
  const [selectedDataset, setSelectedDataset] = useState<DataAsset | null>(null);
  
  // The catalog is served from a shared stale-while-revalidate cache so this
  // page renders instantly on revisit and after a prefetch from the landing.
  const { data: datasets, loading: isLoading } = useDiscoveryCatalog();

  // Real "accessible to me" data, computed server-side from Unity Catalog as
  // the user (OBO). When unavailable (e.g. local dev without a user token) we
  // hide the filter rather than guess.
  const { data: accessInfo } = useAccessibleAssets();
  const accessibleAvailable = accessInfo.available;
  const accessibleIds = useMemo(
    () => new Set(accessInfo.accessible_ids),
    [accessInfo],
  );

  // Contract details for the selected dataset (loaded lazily when modal opens for a dataset)
  const [selectedContract, setSelectedContract] = useState<any | null>(null);
  const [contractError, setContractError] = useState<string | null>(null);
  const [isLoadingContract, setIsLoadingContract] = useState(false);

  // UC table metadata (columns/description) for non-contract tables — loaded
  // lazily when a managed/external/view asset is opened in the modal.
  const [selectedTableDetails, setSelectedTableDetails] = useState<TableDetailsResponse | null>(null);
  const [tableDetailsError, setTableDetailsError] = useState<string | null>(null);
  const [isLoadingTableDetails, setIsLoadingTableDetails] = useState(false);

  useEffect(() => {
    let mounted = true;
    async function loadContract() {
      if (!selectedDataset) {
        setSelectedContract(null);
        setContractError(null);
        return;
      }
      const isDatasetLike = selectedDataset.type === 'dataset' || (selectedDataset as any).type === 'data_product';
      if (!isDatasetLike) {
        setSelectedContract(null);
        setContractError(null);
        return;
      }
      setIsLoadingContract(true);
      setContractError(null);
      try {
        const history = await api.getContractHistory(selectedDataset.id);
        if (!mounted) return;
        const active = history?.find(c => c.is_active) || history?.[0];
        if (!active?.yaml_content) {
          setSelectedContract(null);
          setContractError('No data contract found for this dataset.');
          return;
        }
        const parsed = yaml.load(active.yaml_content) as any;
        setSelectedContract({ ...active, parsed });
      } catch (e: any) {
        if (mounted) setContractError(e?.message || 'Failed to load data contract.');
      } finally {
        if (mounted) setIsLoadingContract(false);
      }
    }
    loadContract();
    return () => { mounted = false; };
  }, [selectedDataset]);

  // Load Unity Catalog metadata when a plain UC table (managed/external/view)
  // is selected, so the Schema tab can show columns + descriptions.
  useEffect(() => {
    let mounted = true;
    async function loadTable() {
      if (!selectedDataset) {
        setSelectedTableDetails(null);
        setTableDetailsError(null);
        return;
      }
      const t = String(selectedDataset.type || '').toLowerCase();
      const isTable = t === 'managed' || t === 'external' || t === 'view';
      if (!isTable) {
        setSelectedTableDetails(null);
        setTableDetailsError(null);
        return;
      }
      const fqn = selectedDataset.id
        || `${selectedDataset.catalog}.${(selectedDataset as any).schema_name}.${selectedDataset.table_name}`;
      if (!fqn || fqn.split('.').length !== 3) {
        setSelectedTableDetails(null);
        setTableDetailsError('Cannot resolve table name.');
        return;
      }
      setIsLoadingTableDetails(true);
      setTableDetailsError(null);
      try {
        const details = await api.getTableDetails(fqn);
        if (!mounted) return;
        setSelectedTableDetails(details);
        // Backend always returns 200; surface in-payload errors here.
        if (details.error_kind === 'not_found') {
          setTableDetailsError(`This table isn't visible in the connected workspace. The Discover catalog may be out of date or the table may have moved.`);
        } else if (details.error_kind === 'permission_denied') {
          setTableDetailsError(`This app's service principal doesn't have access to ${fqn}. Ask a workspace admin to grant USE CATALOG / USE SCHEMA / SELECT on this object so the Discover page can show its columns.`);
        } else if (details.error) {
          setTableDetailsError(details.error);
        }
      } catch (e) {
        if (mounted) {
          setSelectedTableDetails(null);
          setTableDetailsError(e instanceof Error ? e.message : 'Failed to load table details.');
        }
      } finally {
        if (mounted) setIsLoadingTableDetails(false);
      }
    }
    loadTable();
    return () => { mounted = false; };
  }, [selectedDataset]);

  // Forward a submitted question to the inline agent once the panel has
  // mounted and wired its imperative handle.
  useEffect(() => {
    if (!agentQuery) return;
    const id = window.setTimeout(() => chatRef.current?.submitQuery(agentQuery), 0);
    return () => window.clearTimeout(id);
  }, [agentQuery]);

  // Close the domain dropdown on outside click / Escape.
  useEffect(() => {
    if (!showDomainMenu) return;
    const onClick = (e: MouseEvent) => {
      if (domainRef.current && !domainRef.current.contains(e.target as Node)) {
        setShowDomainMenu(false);
      }
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setShowDomainMenu(false); };
    document.addEventListener('mousedown', onClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [showDomainMenu]);

  const toggleDomain = (domain: string) => {
    setSelectedDomains(prev => 
      prev.includes(domain) ? prev.filter(d => d !== domain) : [...prev, domain]
    );
  };

  // "Accessible to me" is backed by the server-computed Unity Catalog access
  // set — an asset is accessible iff its ID is in that set.
  const isAccessibleToMe = (ds: any): boolean => accessibleIds.has(ds.id);

  const filteredDatasets = datasets.filter(ds => {
    const term = effectiveSearchTerm.toLowerCase();
    const matchesSearch = !term ||
      ds.table_name.toLowerCase().includes(term) ||
      (ds.description && ds.description.toLowerCase().includes(term)) ||
      (ds.owner && ds.owner.toLowerCase().includes(term)) ||
      (ds.domain && ds.domain.toLowerCase().includes(term));

    const matchesDomain = selectedDomains.length === 0 || (ds.domain && selectedDomains.includes(ds.domain));

    const matchesType = selectedType === 'all' || normalizeAssetType(ds.type) === selectedType;

    const matchesCertified = !showCertifiedOnly || ds.certified;
    const matchesAccessible = !(showAccessibleOnly && accessibleAvailable) || isAccessibleToMe(ds);

    return matchesSearch && matchesDomain && matchesType && matchesCertified
      && matchesAccessible;
  });

  const activeFilterCount =
    selectedDomains.length +
    (showCertifiedOnly ? 1 : 0) +
    (showAccessibleOnly ? 1 : 0);

  // Count of assets per normalized type, used to label the type pills.
  const typeCounts = datasets.reduce((acc, ds) => {
    const t = normalizeAssetType(ds.type);
    acc[t] = (acc[t] || 0) + 1;
    return acc;
  }, {} as Record<AssetTypeId, number>);

  // Open an asset's detail view by id. Used both by the catalog rails ("View
  // details") and by deep-links from the agent landing.
  const openDetailsById = (id: string) => {
    const found = datasets.find((d) => d.id === id);
    if (found) setSelectedDataset(found);
  };

  // Deep-link from the landing: navigating here with `state.viewAssetId` opens
  // that asset's details once the catalog is available. Clear the state after
  // so a refresh doesn't replay it.
  useEffect(() => {
    const viewId = (location.state as { viewAssetId?: string } | null)?.viewAssetId;
    if (!viewId || datasets.length === 0) return;
    openDetailsById(viewId);
    navigate(location.pathname, { replace: true, state: {} });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.state, datasets]);

  const submitAgentQuery = (q: string) => {
    const trimmed = q.trim();
    if (!trimmed) return;
    // Re-submitting the same text won't retrigger the mount effect, so push
    // directly through the ref when the panel is already open.
    if (agentQuery === trimmed) {
      chatRef.current?.submitQuery(trimmed);
    } else {
      setAgentQuery(trimmed);
    }
  };

  const clearAllFilters = () => {
    setSearchTerm('');
    setSelectedDomains([]);
    setSelectedType('all');
    setShowCertifiedOnly(false);
    setShowAccessibleOnly(false);
  };

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

  // Determine if we should show the landing view or the search results view
  const showResults = effectiveSearchTerm !== '' || selectedType !== 'all' || activeFilterCount > 0;

  return (
    <div className="space-y-6 pb-20">
      {/* Page header — matches the shared Approvals / Requests pattern
          (h1 + descriptive subtitle on the left). */}
      <div>
        <h1 className="text-3xl font-bold text-gray-900 mb-2">Discover</h1>
        <p className="text-gray-600">
          Browse the data catalog, search assets, and explore lineage.
        </p>
      </div>

      {/* Prominent, combined search. Typing live-filters the catalog below.
          Pressing Enter / clicking "Ask AI" ALSO sends the text to the agent
          in an inline panel — we never navigate away from Discover. */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          submitAgentQuery(searchTerm);
        }}
        className="relative group"
      >
        <Search className="absolute left-5 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400 group-focus-within:text-primary transition-colors" />
        <input
          type="text"
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
          placeholder="Search the catalog — or ask a question and press Enter"
          aria-label="Search the catalog or ask the agent"
          className="w-full pl-14 pr-40 py-5 bg-white border border-gray-200 rounded-2xl text-lg shadow-sm focus:ring-2 focus:ring-primary/40 focus:border-primary transition-all outline-none"
        />

        <button
          type="submit"
          disabled={!searchTerm.trim()}
          title="Ask the agent (Enter)"
          className="absolute right-2.5 top-1/2 -translate-y-1/2 inline-flex items-center gap-1.5 px-4 py-2 rounded-xl bg-primary text-white text-sm font-medium shadow-sm hover:bg-primary/90 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <Sparkles className="w-4 h-4" />
          <span className="hidden sm:inline">Ask AI</span>
        </button>
      </form>

      {/* Filter chip bar. Type pills follow the canonical order: Data Products
          → Datasets → Dashboards → Apps → Genie Spaces → leftover (Tables).
          "Accessible to me" and "Certified" are first-class toggle chips; the
          Filters popover (right) holds the remaining advanced controls. */}
      <div className="flex flex-wrap gap-2 items-center">
        <button
          onClick={() => setSelectedType('all')}
          className={`px-4 py-2 rounded-full text-sm font-medium flex items-center gap-2 transition-colors border ${
            selectedType === 'all'
              ? 'bg-gray-900 text-white border-gray-900 shadow-sm'
              : 'bg-white text-gray-700 border-gray-200 hover:bg-gray-50'
          }`}
        >
          All
        </button>

        {/* First-class scope chips. "Accessible to me" only appears when the
            backend can compute real Unity Catalog access for the user. */}
        {accessibleAvailable && (
          <button
            onClick={() => setShowAccessibleOnly((v) => !v)}
            aria-pressed={showAccessibleOnly}
            className={`px-4 py-2 rounded-full text-sm font-medium flex items-center gap-2 transition-colors border ${
              showAccessibleOnly
                ? 'bg-primary/10 text-primary border-primary/30 shadow-sm'
                : 'bg-white text-gray-700 border-gray-200 hover:bg-gray-50'
            }`}
          >
            <UserCheck className="w-4 h-4" /> Accessible to me
          </button>
        )}
        <button
          onClick={() => setShowCertifiedOnly((v) => !v)}
          aria-pressed={showCertifiedOnly}
          className={`px-4 py-2 rounded-full text-sm font-medium flex items-center gap-2 transition-colors border ${
            showCertifiedOnly
              ? 'bg-green-50 text-green-800 border-green-200 shadow-sm'
              : 'bg-white text-gray-700 border-gray-200 hover:bg-gray-50'
          }`}
        >
          <ShieldCheck className="w-4 h-4" /> Certified
        </button>

        {/* Domain selector — chip-style dropdown. */}
        {uniqueDomains.length > 0 && (
          <div ref={domainRef} className="relative">
            <button
              type="button"
              onClick={() => setShowDomainMenu((v) => !v)}
              aria-expanded={showDomainMenu}
              className={`px-4 py-2 rounded-full text-sm font-medium flex items-center gap-2 transition-colors border ${
                selectedDomains.length > 0
                  ? 'bg-primary/10 text-primary border-primary/30 shadow-sm'
                  : 'bg-white text-gray-700 border-gray-200 hover:bg-gray-50'
              }`}
            >
              <Database className="w-4 h-4" />
              {selectedDomains.length === 0
                ? 'All domains'
                : selectedDomains.length === 1
                  ? selectedDomains[0]
                  : `${selectedDomains.length} domains`}
              <ChevronDown className={`w-4 h-4 transition-transform ${showDomainMenu ? 'rotate-180' : ''}`} />
            </button>

            {showDomainMenu && (
              <div className="absolute left-0 top-full mt-2 w-64 bg-white rounded-xl shadow-xl border border-gray-200 p-2 z-40 animate-in fade-in slide-in-from-top-1 duration-150 text-left">
                <div className="flex items-center justify-between px-2 py-1.5">
                  <span className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">Domains</span>
                  {selectedDomains.length > 0 && (
                    <button
                      type="button"
                      onClick={() => setSelectedDomains([])}
                      className="text-xs text-gray-500 hover:text-gray-900"
                    >
                      Clear
                    </button>
                  )}
                </div>
                <div className="max-h-64 overflow-y-auto pr-1 space-y-0.5 custom-scrollbar">
                  {uniqueDomains.map((domain) => {
                    const Icon = getDomainIcon(domain as string);
                    const checked = selectedDomains.includes(domain as string);
                    return (
                      <label
                        key={domain as string}
                        className="flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-gray-50 cursor-pointer text-sm text-gray-700"
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => toggleDomain(domain as string)}
                          className="rounded border-gray-300 text-primary focus:ring-primary/40"
                        />
                        <Icon className="w-4 h-4 text-gray-400" />
                        <span className="truncate">{domain}</span>
                      </label>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )}

        <span className="h-6 w-px bg-gray-200 mx-1" aria-hidden="true" />

        {/* Always surface the core categories (Data Products, Datasets,
            Dashboards) even when empty so users know they exist; show the
            rest only when populated. Jobs are intentionally excluded. */}
        {ASSET_TYPE_ORDER
          .filter((t) => t !== 'job' && (
            (typeCounts[t] || 0) > 0 || t === 'data_product' || t === 'dataset' || t === 'dashboard'
          ))
          .map((t) => {
            const meta = ASSET_TYPES[t];
            const Icon = meta.icon;
            const active = selectedType === t;
            const count = typeCounts[t] || 0;
            return (
              <button
                key={t}
                onClick={() => setSelectedType(active ? 'all' : t)}
                className={`px-4 py-2 rounded-full text-sm font-medium flex items-center gap-2 transition-colors border ${
                  active
                    ? `${meta.accentBg} ${meta.accentText} ${meta.accentBorder} shadow-sm`
                    : 'bg-white text-gray-700 border-gray-200 hover:bg-gray-50'
                }`}
              >
                <Icon className="w-4 h-4" /> {meta.plural}
                <span className={`text-xs ${active ? 'opacity-70' : 'text-gray-400'}`}>{count}</span>
              </button>
            );
          })}

      </div>

      {/* Inline agent panel — answers questions without leaving the page. */}
      {agentQuery && (
        <div className="bg-white rounded-2xl border border-primary/20 shadow-sm overflow-hidden animate-in fade-in slide-in-from-top-2 duration-300">
          <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100 bg-primary/5">
            <div className="flex items-center gap-2 text-sm font-semibold text-gray-800">
              <Sparkles className="w-4 h-4 text-primary" /> Agent
            </div>
            <button
              type="button"
              onClick={() => setAgentQuery(null)}
              className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors"
              title="Close agent"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
          <div className="h-[440px] p-4">
            <ChatView
              ref={chatRef}
              storageKey={INLINE_AGENT_STORAGE_KEY}
              placeholder="Ask a follow-up question..."
              onRoute={(route) => navigate(route.path)}
            />
          </div>
        </div>
      )}

      {!showResults ? (
        <div className="space-y-10 animate-in fade-in duration-500">
          {/* Catalog 101 — teaches the asset vocabulary before domains. */}
          <AssetTaxonomyExplainer />

          {/* Shared rails (Pinned → Data Products → Datasets), reused from the
              agent landing. "Browse all" filters in place; "View details" opens
              the asset's detail panel. */}
          <CatalogRails
            onViewDetails={(ref) => openDetailsById(ref.id)}
            onBrowseAll={(target) => setSelectedType(target)}
          />
        </div>
      ) : (
        /* Table Section (Search Results) */
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden animate-in fade-in slide-in-from-bottom-4 duration-300">
          <div className="p-4 border-b border-gray-100 bg-gray-50/50 flex justify-between items-center">
            <h3 className="font-semibold text-gray-700">
              {filteredDatasets.length} {filteredDatasets.length === 1 ? 'result' : 'results'} found
            </h3>
            <button
              onClick={clearAllFilters}
              className="text-sm text-gray-500 hover:text-gray-900 flex items-center gap-1"
            >
              <X className="w-4 h-4" /> Clear all filters
            </button>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="bg-gray-50 border-b border-gray-200">
                  <th className="px-6 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider">Type</th>
                  <th className="px-6 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider">Name</th>
                  <th className="px-6 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider">Domain</th>
                  <th className="px-6 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider">Location</th>
                  <th className="px-6 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider">Description</th>
                  <th className="px-6 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider text-left">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {isLoading ? (
                  <tr>
                    <td colSpan={6} className="px-6 py-12 text-center text-sm text-gray-500">
                      <div className="flex flex-col items-center justify-center space-y-3">
                        <div className="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin" />
                        <p>Loading data assets...</p>
                      </div>
                    </td>
                  </tr>
                ) : filteredDatasets.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="px-6 py-12 text-center text-sm text-gray-500">
                      No assets found matching your criteria.
                    </td>
                  </tr>
                ) : filteredDatasets.map((ds) => (
                  <tr key={ds.id} className="hover:bg-gray-50 transition-colors group">
                    <td className="px-6 py-4 whitespace-nowrap align-middle">
                      <AssetTypeBadge type={ds.type} />
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
                    <td className="px-6 py-4 whitespace-nowrap align-middle text-sm font-medium text-gray-900">{ds.domain}</td>
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
                        {(() => {
                          const href = assetWorkspaceUrl(databricksWorkspaceUrl, ds);
                          if (!href) return null;
                          return (
                            <a
                              href={href}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-gray-500 hover:text-primary font-medium transition-colors inline-flex items-center gap-1"
                              title={workspaceLinkLabel(ds.type)}
                            >
                              <ExternalLink className="w-3.5 h-3.5" />
                              {workspaceLinkLabel(ds.type)}
                            </a>
                          );
                        })()}
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
        <DetailsModal
          asset={selectedDataset}
          contract={selectedContract}
          isLoadingContract={isLoadingContract}
          contractError={contractError}
          tableDetails={selectedTableDetails}
          isLoadingTableDetails={isLoadingTableDetails}
          tableDetailsError={tableDetailsError}
          workspaceUrl={databricksWorkspaceUrl}
          onClose={() => setSelectedDataset(null)}
          canRequestAccess={canRequestAccess(selectedDataset.type)}
          onRequestAccess={() => {
            handleRequestAccess(selectedDataset);
            setSelectedDataset(null);
          }}
        />
      )}
    </div>
  );
}

interface DetailsModalProps {
  asset: any;
  contract: any | null;
  isLoadingContract: boolean;
  contractError: string | null;
  tableDetails: TableDetailsResponse | null;
  isLoadingTableDetails: boolean;
  tableDetailsError: string | null;
  workspaceUrl: string;
  onClose: () => void;
  canRequestAccess: boolean;
  onRequestAccess: () => void;
}

function DetailsModal({
  asset,
  contract,
  isLoadingContract,
  contractError,
  tableDetails,
  isLoadingTableDetails,
  tableDetailsError,
  workspaceUrl,
  onClose,
  canRequestAccess,
  onRequestAccess,
}: DetailsModalProps) {
  const isDataset = asset.type === 'dataset' || asset.type === 'data_product';
  const isTableLike = ['managed', 'external', 'view'].includes(String(asset.type).toLowerCase());

  // Helpers for derived contract info (safe when contract is null)
  const parsed = contract?.parsed || null;
  const purpose = parsed?.description?.purpose;
  const limitations = parsed?.description?.limitations;
  const usage = parsed?.description?.usage;
  const servers: any[] = Array.isArray(parsed?.servers) ? parsed.servers : [];
  const schema: any[] = Array.isArray(parsed?.schema) ? parsed.schema : [];
  const team: any[] = Array.isArray(parsed?.team) ? parsed.team : [];
  const slaProperties: any[] = Array.isArray(parsed?.slaProperties) ? parsed.slaProperties : [];
  const support: any[] = Array.isArray(parsed?.support) ? parsed.support : [];
  const authoritativeDefinitions: any[] = Array.isArray(parsed?.authoritativeDefinitions)
    ? parsed.authoritativeDefinitions
    : [];

  // Owner derivation: prefer asset.owner, then contract author/creator role
  const contractOwner = team.find((t: any) => /owner|steward/i.test(String(t?.role || '')))?.username
    || parsed?.dataProductOwner
    || null;

  // Type display string
  const displayType = isDataset
    ? (parsed?.kind === 'DataContract' ? 'Data Product' : 'Dataset')
    : asset.type;

  // Aggregate dataset-wide classification from any table customProperty (e.g., "u-nnpi", "PII")
  const datasetClassifications = Array.from(new Set(
    schema.flatMap((tbl: any) => {
      const cps = Array.isArray(tbl?.customProperties) ? tbl.customProperties : [];
      return cps
        .filter((cp: any) => /classification/i.test(String(cp?.property || '')))
        .map((cp: any) => String(cp?.value || '').trim())
        .filter(Boolean);
    })
  ));

  // Catalog Explorer / workspace deep links.
  // For datasets, prefer the schema declared by the contract's first server.
  const primaryServer = servers[0] || null;
  const datasetSchemaHref = primaryServer
    ? catalogExplorerUrl(workspaceUrl, primaryServer.catalog, primaryServer.schema)
    : null;
  const directAssetHref = assetWorkspaceUrl(workspaceUrl, asset);
  // What we link to from the header chip / footer button.
  const headerHref = isDataset ? datasetSchemaHref : directAssetHref;
  const headerLinkLabel = isDataset
    ? 'Open Schema in Catalog Explorer'
    : workspaceLinkLabel(asset.type);

  // Build seed tables for the Lineage tab.
  const lineageSeeds: LineageSeedTable[] = useMemo(() => {
    if (isDataset && primaryServer && schema.length > 0) {
      return schema.map((tbl: any) => {
        const physical = tbl.physicalName || tbl.name;
        const fqn = `${primaryServer.catalog}.${primaryServer.schema}.${physical}`;
        const cps = Array.isArray(tbl.customProperties) ? tbl.customProperties : [];
        const getCp = (key: string) => cps.find((p: any) => String(p?.property || '').toLowerCase() === key)?.value;
        const ups = getCp('upstream_tables');
        const downs = getCp('downstream_tables');
        return {
          fqn,
          displayName: physical,
          classification: getCp('classification') || null,
          upstreams: Array.isArray(ups) ? ups : undefined,
          downstreams: Array.isArray(downs) ? downs : undefined,
        } satisfies LineageSeedTable;
      });
    }
    if (isTableLike && asset.catalog && asset.schema_name && asset.table_name) {
      return [
        {
          fqn: `${asset.catalog}.${asset.schema_name}.${asset.table_name}`,
          displayName: asset.table_name,
        },
      ];
    }
    return [];
  }, [isDataset, isTableLike, primaryServer, schema, asset.catalog, asset.schema_name, asset.table_name]);
  const hasLineage = lineageSeeds.length > 0;
  // Schema tab: from contract YAML (datasets) OR live UC columns (plain tables).
  // For UC tables we render the tab as soon as we know the asset is a table —
  // the actual content shows a loader/error/empty state if needed.
  const tableColumns = tableDetails?.columns ?? [];
  const hasSchema = (isDataset && schema.length > 0)
    || (isTableLike && (tableColumns.length > 0 || isLoadingTableDetails || !!tableDetailsError));
  const schemaTabCount = isDataset ? schema.length : tableColumns.length;
  const hasQuality = (slaProperties.length > 0 || asset.sla)
    || (asset.data_quality && Object.keys(asset.data_quality).length > 0);
  const hasTeam = isDataset && (team.length > 0 || support.length > 0 || authoritativeDefinitions.length > 0);

  type TabId = 'overview' | 'schema' | 'lineage' | 'quality' | 'team';
  const tabs: { id: TabId; label: string; icon: ReactNode; count?: number }[] = [
    { id: 'overview', label: 'Overview', icon: <BookOpen className="w-4 h-4" /> },
    ...(hasSchema
      ? [{
          id: 'schema' as TabId,
          label: isDataset ? 'Schema' : 'Columns',
          icon: <TableIcon className="w-4 h-4" />,
          ...(schemaTabCount > 0 ? { count: schemaTabCount } : {}),
        }]
      : []),
    ...(hasLineage
      ? [{ id: 'lineage' as TabId, label: 'Lineage', icon: <Network className="w-4 h-4" /> }]
      : []),
    ...(hasQuality
      ? [{ id: 'quality' as TabId, label: 'Quality', icon: <Activity className="w-4 h-4" /> }]
      : []),
    ...(hasTeam
      ? [{ id: 'team' as TabId, label: 'Team & Support', icon: <Users className="w-4 h-4" /> }]
      : []),
  ];
  const [activeTab, setActiveTab] = useState<TabId>('overview');
  // If contract loads asynchronously and adds new tabs, current tab may have
  // become unavailable (e.g., user opened a non-contract asset, then tab list
  // changed). Reset to overview if that happens.
  useEffect(() => {
    if (!tabs.some((t) => t.id === activeTab)) setActiveTab('overview');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tabs.length]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-sm transition-opacity animate-in fade-in duration-200">
      <div className="bg-white rounded-xl shadow-2xl max-w-5xl w-full max-h-[90vh] overflow-hidden flex flex-col transform transition-all animate-in zoom-in-95 duration-200">

        {/* Header */}
        <div className="p-6 pb-4 flex justify-between items-start bg-white">
          <div className="min-w-0">
            <div className="flex items-center gap-3 mb-1 flex-wrap">
              <h2 className="text-xl font-bold text-gray-900 truncate">
                {parsed?.dataProduct || asset.table_name}
              </h2>
              {asset.certified && (
                <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-green-100 text-green-800 border border-green-200">
                  <ShieldCheck className="w-3 h-3 mr-1" /> Certified
                </span>
              )}
              {parsed?.version && (
                <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-700 border border-gray-200">
                  <GitBranch className="w-3 h-3 mr-1" /> v{parsed.version}
                </span>
              )}
              {parsed?.status && (
                <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-blue-50 text-blue-700 border border-blue-100 capitalize">
                  {parsed.status}
                </span>
              )}
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              <p className="text-sm text-gray-500 font-mono bg-gray-50 px-2 py-1 rounded inline-block border border-gray-100 truncate max-w-full">
                {`${asset.catalog}.${asset.schema_name}${asset.table_name && !isDataset ? `.${asset.table_name}` : ''}`}
              </p>
              {headerHref && (
                <a
                  href={headerHref}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
                  title={headerLinkLabel}
                >
                  <ExternalLink className="w-3.5 h-3.5" />
                  {headerLinkLabel}
                </a>
              )}
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-2 rounded-lg hover:bg-gray-100 text-gray-400 hover:text-gray-600 transition-colors focus:outline-none focus:ring-2 focus:ring-primary/50 shrink-0 ml-3"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Tab Bar */}
        <div className="border-b border-gray-200 bg-white px-3 sm:px-6">
          <nav className="flex gap-1 overflow-x-auto" aria-label="Asset detail tabs">
            {tabs.map((tab) => {
              const isActive = activeTab === tab.id;
              return (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => setActiveTab(tab.id)}
                  className={`flex items-center gap-2 px-3 py-2.5 -mb-px text-sm font-medium border-b-2 transition-colors whitespace-nowrap ${
                    isActive
                      ? 'border-primary text-primary'
                      : 'border-transparent text-gray-500 hover:text-gray-900 hover:border-gray-300'
                  }`}
                  aria-current={isActive ? 'page' : undefined}
                >
                  <span className={isActive ? 'text-primary' : 'text-gray-400'}>{tab.icon}</span>
                  {tab.label}
                  {typeof tab.count === 'number' && (
                    <span className={`px-1.5 py-0.5 rounded-full text-[10px] font-semibold ${
                      isActive ? 'bg-primary/10 text-primary' : 'bg-gray-100 text-gray-600'
                    }`}>
                      {tab.count}
                    </span>
                  )}
                </button>
              );
            })}
          </nav>
        </div>

        {/* Scrollable Content */}
        <div className="overflow-y-auto flex-1 p-4 sm:p-6 space-y-6">

          {/* Contract loading / error states (visible regardless of tab so users get feedback) */}
          {isDataset && isLoadingContract && (
            <div className="flex items-center gap-2 text-sm text-gray-500 bg-gray-50 border border-gray-100 rounded-lg p-4">
              <Loader2 className="w-4 h-4 animate-spin" /> Loading data contract…
            </div>
          )}
          {isDataset && !isLoadingContract && contractError && (
            <div className="flex items-start gap-2 text-sm text-amber-800 bg-amber-50 border border-amber-100 rounded-lg p-3">
              <AlertCircle className="w-4 h-4 mt-0.5 shrink-0 text-amber-600" />
              <span>{contractError}</span>
            </div>
          )}

          {/* ============== Overview tab ============== */}
          {activeTab === 'overview' && (
            <>
              {/* Description */}
              <section>
                <SectionHeading icon={<BookOpen className="w-4 h-4" />} title="Description" />
                {(purpose || limitations || usage) ? (
                  <div className="space-y-3">
                    {purpose && (
                      <div className="bg-gray-50 p-4 rounded-lg border border-gray-100">
                        <div className="text-[11px] font-semibold uppercase tracking-wider text-gray-500 mb-1">Purpose</div>
                        <p className="text-sm text-gray-700 leading-relaxed">{purpose}</p>
                      </div>
                    )}
                    {usage && (
                      <div className="bg-gray-50 p-4 rounded-lg border border-gray-100">
                        <div className="text-[11px] font-semibold uppercase tracking-wider text-gray-500 mb-1">Usage</div>
                        <p className="text-sm text-gray-700 leading-relaxed">{usage}</p>
                      </div>
                    )}
                    {limitations && (
                      <div className="bg-amber-50 p-4 rounded-lg border border-amber-100">
                        <div className="text-[11px] font-semibold uppercase tracking-wider text-amber-700 mb-1">Limitations</div>
                        <p className="text-sm text-amber-900 leading-relaxed">{limitations}</p>
                      </div>
                    )}
                  </div>
                ) : (
                  <p className="text-sm text-gray-600 leading-relaxed bg-gray-50 p-4 rounded-lg border border-gray-100">
                    {asset.description || 'No description provided.'}
                  </p>
                )}
              </section>

              {/* Governance */}
              <section>
                <SectionHeading icon={<ShieldCheck className="w-4 h-4" />} title="Governance" />
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <MetaRow label="Domain" value={parsed?.domain || asset.domain} />
                  <MetaRow label="Owner" value={contractOwner || asset.owner} />
                  <MetaRow label="Type">
                    <span className="text-sm font-medium text-gray-900 bg-gray-100 px-2 py-0.5 rounded capitalize">{displayType}</span>
                  </MetaRow>
                  {datasetClassifications.length > 0 && (
                    <MetaRow label="Classification">
                      <div className="flex flex-wrap gap-1 justify-end">
                        {datasetClassifications.map((c) => (
                          <span key={c} className="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold bg-rose-50 text-rose-700 border border-rose-100 uppercase">
                            <Lock className="w-3 h-3 mr-1" /> {c}
                          </span>
                        ))}
                      </div>
                    </MetaRow>
                  )}
                  {parsed?.id && <MetaRow label="Contract ID" value={parsed.id} mono />}
                  {asset.last_synced_at && <MetaRow label="Last Synced" value={formatDate(asset.last_synced_at)} />}
                  {asset.created_at && <MetaRow label="Created" value={formatDate(asset.created_at)} />}
                </div>
              </section>

              {/* Servers (datasets only) */}
              {isDataset && servers.length > 0 && (
                <section>
                  <SectionHeading icon={<Server className="w-4 h-4" />} title="Servers" count={servers.length} />
                  <div className="space-y-2">
                    {servers.map((s: any, i: number) => {
                      const serverHref = catalogExplorerUrl(workspaceUrl, s.catalog, s.schema);
                      return (
                        <div key={s.id || i} className="border border-gray-200 rounded-lg p-3 bg-white">
                          <div className="flex flex-wrap items-center justify-between gap-2 mb-1">
                            <div className="flex flex-wrap items-center gap-2 min-w-0">
                              <span className="text-sm font-semibold text-gray-900 capitalize truncate">{s.id || s.environment || `Server ${i + 1}`}</span>
                              {s.type && (
                                <span className="px-2 py-0.5 rounded text-[11px] font-medium bg-blue-50 text-blue-700 border border-blue-100">
                                  {s.type}
                                </span>
                              )}
                            </div>
                            {serverHref && (
                              <a
                                href={serverHref}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="inline-flex items-center gap-1 text-[11px] font-medium text-primary hover:underline shrink-0"
                                title="Open schema in Catalog Explorer"
                              >
                                <ExternalLink className="w-3 h-3" /> Open
                              </a>
                            )}
                          </div>
                          <div className="font-mono text-xs text-gray-600 truncate" title={`${s.catalog || ''}.${s.schema || ''}`}>
                            {[s.catalog, s.schema].filter(Boolean).join('.') || s.host || '—'}
                          </div>
                          {s.host && <div className="text-[11px] text-gray-400 mt-0.5">{s.host}</div>}
                        </div>
                      );
                    })}
                  </div>
                </section>
              )}

              {/* Tags */}
              {Array.isArray(asset.tags) && asset.tags.length > 0 && (
                <section>
                  <SectionHeading icon={<Tag className="w-4 h-4" />} title="Tags" count={asset.tags.length} />
                  <div className="flex flex-wrap gap-2">
                    {asset.tags.map((tag: string) => (
                      <span key={tag} className="inline-flex items-center px-2.5 py-1 rounded-md text-xs font-medium bg-blue-50 text-blue-700 border border-blue-100 font-mono">
                        {tag}
                      </span>
                    ))}
                  </div>
                </section>
              )}

              {/* Empty-state hint for sparse table-likes */}
              {isTableLike && !asset.description && (!asset.tags || asset.tags.length === 0) && !asset.data_quality && (
                <div className="bg-gray-50 border border-gray-200 border-dashed rounded-lg p-4 text-center">
                  <p className="text-sm text-gray-500">No additional metadata available for this {String(asset.type).toLowerCase()}.</p>
                </div>
              )}
            </>
          )}

          {/* ============== Schema tab ============== */}
          {activeTab === 'schema' && hasSchema && (
            <>
              {/* Dataset (contract YAML) view */}
              {isDataset && schema.length > 0 && (
                <section>
                  <SectionHeading
                    icon={<TableIcon className="w-4 h-4" />}
                    title="Tables & Columns"
                    count={schema.length}
                  />
                  <p className="text-xs text-gray-500 mb-3">
                    Click a table to see its columns, access groups, and tags. View the data flow visually in the Lineage tab.
                  </p>
                  <div className="space-y-2">
                    {schema.map((tbl: any, i: number) => (
                      <SchemaTableEntry
                        key={tbl.id || i}
                        tbl={tbl}
                        expandedByDefault={schema.length === 1}
                        workspaceUrl={workspaceUrl}
                        catalog={primaryServer?.catalog}
                        schema={primaryServer?.schema}
                      />
                    ))}
                  </div>
                </section>
              )}

              {/* UC table (live columns) view */}
              {!isDataset && isTableLike && (
                <section>
                  <SectionHeading
                    icon={<Columns3 className="w-4 h-4" />}
                    title="Columns"
                    {...(tableColumns.length > 0 ? { count: tableColumns.length } : {})}
                  />
                  {isLoadingTableDetails && (
                    <div className="flex items-center gap-2 text-sm text-gray-500 bg-gray-50 border border-gray-100 rounded-lg p-4">
                      <Loader2 className="w-4 h-4 animate-spin" /> Loading table schema…
                    </div>
                  )}
                  {!isLoadingTableDetails && tableDetailsError && (
                    <div className="flex items-start gap-2 text-sm text-amber-800 bg-amber-50 border border-amber-100 rounded-lg p-3">
                      <AlertCircle className="w-4 h-4 mt-0.5 shrink-0 text-amber-600" />
                      <span>{tableDetailsError}</span>
                    </div>
                  )}
                  {!isLoadingTableDetails && !tableDetailsError && tableColumns.length === 0 && (
                    <div className="bg-gray-50 border border-gray-200 border-dashed rounded-lg p-4 text-center">
                      <p className="text-sm text-gray-500">No columns reported for this table.</p>
                    </div>
                  )}
                  {tableColumns.length > 0 && (
                    <div className="rounded-md border border-gray-200 overflow-hidden">
                      <div className="bg-gray-50 px-3 py-2 border-b border-gray-200 text-[11px] font-semibold uppercase tracking-wide text-gray-500 flex items-center justify-between">
                        <span className="flex items-center gap-1.5">
                          <Columns3 className="w-3 h-3" /> {tableColumns.length} column{tableColumns.length === 1 ? '' : 's'}
                        </span>
                        {tableDetails?.owner && (
                          <span className="normal-case font-normal text-gray-500">
                            Owner: <span className="font-medium text-gray-700">{tableDetails.owner}</span>
                          </span>
                        )}
                      </div>
                      <table className="w-full text-xs">
                        <thead className="bg-white text-gray-500 border-b border-gray-100">
                          <tr>
                            <th className="text-left font-medium px-3 py-1.5">Name</th>
                            <th className="text-left font-medium px-3 py-1.5">Type</th>
                            <th className="text-left font-medium px-3 py-1.5">Description</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-100">
                          {[...tableColumns]
                            .sort((a, b) => (a.position ?? 0) - (b.position ?? 0))
                            .map((c, i) => (
                              <tr key={`${c.name}-${i}`} className="hover:bg-gray-50/60">
                                <td className="px-3 py-1.5 align-top font-mono text-gray-900 break-all">
                                  {c.name}
                                </td>
                                <td className="px-3 py-1.5 align-top text-gray-600 font-mono whitespace-nowrap">
                                  {c.type || '—'}
                                  {c.nullable === false && (
                                    <span className="ml-1 text-rose-500" title="Required (NOT NULL)">*</span>
                                  )}
                                </td>
                                <td className="px-3 py-1.5 align-top text-gray-600 leading-snug">
                                  {c.comment || <span className="text-gray-400 italic">—</span>}
                                </td>
                              </tr>
                            ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                  {tableDetails?.tags && Object.keys(tableDetails.tags).length > 0 && (
                    <div className="mt-4">
                      <SectionHeading icon={<Tag className="w-4 h-4" />} title="UC Tags" count={Object.keys(tableDetails.tags).length} />
                      <div className="flex flex-wrap gap-2">
                        {Object.entries(tableDetails.tags).map(([k, v]) => (
                          <span key={k} className="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium bg-blue-50 text-blue-700 border border-blue-100 font-mono">
                            {k}{v ? `=${v}` : ''}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                </section>
              )}
            </>
          )}

          {/* ============== Lineage tab ============== */}
          {activeTab === 'lineage' && hasLineage && (
            <section>
              <div className="mb-3">
                <SectionHeading icon={<Network className="w-4 h-4" />} title="Lineage" />
                <p className="text-xs text-gray-500 -mt-1">
                  Click <span className="font-medium">Sources</span> on any table to see what feeds it, or
                  {' '}<span className="font-medium">Consumers</span> to see what depends on it. The
                  {' '}<ExternalLink className="inline w-3 h-3 align-text-bottom" /> icon opens that table in Catalog Explorer.
                </p>
              </div>
              {/* Fixed height keeps React Flow's container dimensions definite,
                  which is required for it to render the canvas correctly. */}
              <div className="h-[520px]">
                <LineageGraph seedTables={lineageSeeds} workspaceUrl={workspaceUrl} height="100%" />
              </div>
            </section>
          )}

          {/* ============== Quality tab ============== */}
          {activeTab === 'quality' && hasQuality && (
            <>
              {(slaProperties.length > 0 || asset.sla) && (
                <section>
                  <SectionHeading icon={<Calendar className="w-4 h-4" />} title="Service Level" />
                  {slaProperties.length > 0 ? (
                    <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
                      {slaProperties.map((sp: any, i: number) => (
                        <div key={i} className="flex justify-between text-sm p-3 border-b border-gray-100 last:border-b-0">
                          <span className="text-gray-500 capitalize">{String(sp.property || '').replace(/([A-Z])/g, ' $1').trim()}</span>
                          <span className="font-medium text-gray-900">
                            {sp.value} {sp.unit ? <span className="text-gray-500">{sp.unit}</span> : null}
                          </span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-sm text-gray-700 bg-amber-50 text-amber-900 border border-amber-100 p-3 rounded-lg flex items-center">
                      <Info className="w-4 h-4 mr-2 shrink-0 text-amber-600" />
                      {asset.sla}
                    </p>
                  )}
                </section>
              )}
              {asset.data_quality && Object.keys(asset.data_quality).length > 0 && (
                <section>
                  <SectionHeading icon={<Activity className="w-4 h-4" />} title="Data Quality" />
                  <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
                    {Object.entries(asset.data_quality).map(([k, v], i, arr) => (
                      <div key={k} className={`flex justify-between text-sm p-3 ${i === arr.length - 1 ? '' : 'border-b border-gray-100'}`}>
                        <span className="text-gray-500 capitalize">{k.replace(/_/g, ' ')}</span>
                        <span className="font-medium text-gray-900">{String(v)}</span>
                      </div>
                    ))}
                  </div>
                </section>
              )}
            </>
          )}

          {/* ============== Team & Support tab ============== */}
          {activeTab === 'team' && hasTeam && (
            <>
              {team.length > 0 && (
                <section>
                  <SectionHeading icon={<Users className="w-4 h-4" />} title="Team" count={team.length} />
                  <div className="border border-gray-200 rounded-lg overflow-hidden">
                    {team.map((t: any, i: number) => (
                      <div key={i} className="flex flex-wrap items-center justify-between gap-2 p-3 border-b border-gray-100 last:border-b-0 text-sm">
                        <div className="flex items-center gap-2 min-w-0">
                          <span className="font-medium text-gray-900 truncate">{t.username || t.name || '—'}</span>
                          {t.role && (
                            <span className="px-2 py-0.5 rounded text-[11px] font-medium bg-gray-100 text-gray-700 border border-gray-200 capitalize">{t.role}</span>
                          )}
                        </div>
                        {t.dateIn && <span className="text-xs text-gray-400">since {t.dateIn}</span>}
                      </div>
                    ))}
                  </div>
                </section>
              )}
              {support.length > 0 && (
                <section>
                  <SectionHeading title="Support" />
                  <div className="space-y-1.5">
                    {support.map((s: any, i: number) => (
                      <div key={i} className="text-sm flex items-center gap-2">
                        {s.channel && <span className="font-medium text-gray-700 capitalize">{s.channel}:</span>}
                        {s.url ? (
                          <a href={s.url} target="_blank" rel="noopener noreferrer" className="text-primary hover:underline truncate">{s.url}</a>
                        ) : (
                          <span className="text-gray-600">{s.tool || s.description || '—'}</span>
                        )}
                      </div>
                    ))}
                  </div>
                </section>
              )}
              {authoritativeDefinitions.length > 0 && (
                <section>
                  <SectionHeading icon={<LinkIcon className="w-4 h-4" />} title="References" count={authoritativeDefinitions.length} />
                  <div className="space-y-1.5 text-sm">
                    {authoritativeDefinitions.map((d: any, i: number) => (
                      <div key={i} className="flex flex-wrap items-center gap-2">
                        {d.type && (
                          <span className="px-2 py-0.5 rounded text-[11px] font-medium bg-gray-100 text-gray-700 border border-gray-200 capitalize">
                            {d.type}
                          </span>
                        )}
                        {d.url ? (
                          <a href={d.url} target="_blank" rel="noopener noreferrer" className="text-primary hover:underline truncate">
                            {d.url}
                          </a>
                        ) : (
                          <span className="text-gray-700">{d.description || '—'}</span>
                        )}
                        {d.url && d.description && (
                          <span className="text-xs text-gray-500 truncate basis-full sm:basis-auto">{d.description}</span>
                        )}
                      </div>
                    ))}
                  </div>
                </section>
              )}
            </>
          )}
        </div>

        {/* Footer */}
        <div className="p-4 border-t border-gray-100 bg-gray-50 flex flex-wrap justify-end gap-3 shrink-0">
          <button
            onClick={onClose}
            className="px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-100 transition-colors"
          >
            Close
          </button>
          {headerHref && (
            <a
              href={headerHref}
              target="_blank"
              rel="noopener noreferrer"
              className="px-4 py-2 border border-gray-300 bg-white rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50 transition-colors flex items-center gap-2"
            >
              <ExternalLink className="w-4 h-4" /> {headerLinkLabel}
            </a>
          )}
          {isDataset && contract?.yaml_content && (
            <button
              onClick={() => {
                const blob = new Blob([contract.yaml_content], { type: 'text/yaml' });
                const url = URL.createObjectURL(blob);
                window.open(url, '_blank');
                setTimeout(() => URL.revokeObjectURL(url), 30_000);
              }}
              className="px-4 py-2 border border-gray-300 bg-white rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50 transition-colors flex items-center gap-2"
            >
              <FileText className="w-4 h-4" /> View Contract YAML
            </button>
          )}
          {!isDataset && asset.certified && asset.contract_url && (
            <a
              href={asset.contract_url}
              target="_blank"
              rel="noopener noreferrer"
              className="px-4 py-2 border border-gray-300 bg-white rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50 transition-colors"
            >
              View Contract
            </a>
          )}
          {canRequestAccess && (
            <button
              onClick={onRequestAccess}
              className="px-6 py-2 bg-primary text-white rounded-lg text-sm font-medium hover:bg-primary/90 transition-colors shadow-sm"
            >
              Request Access
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function SchemaTableEntry({
  tbl,
  expandedByDefault,
  workspaceUrl,
  catalog,
  schema,
}: {
  tbl: any;
  expandedByDefault?: boolean;
  workspaceUrl?: string;
  catalog?: string | null;
  schema?: string | null;
}) {
  const [open, setOpen] = useState(!!expandedByDefault);
  const cols: any[] = Array.isArray(tbl.properties) ? tbl.properties : [];
  const tags: string[] = Array.isArray(tbl.tags) ? tbl.tags : [];
  const customProps: any[] = Array.isArray(tbl.customProperties) ? tbl.customProperties : [];

  const getCustom = (name: string): any => {
    const cp = customProps.find((p: any) => String(p?.property || '').toLowerCase() === name.toLowerCase());
    return cp?.value;
  };
  const classification = getCustom('classification');
  const tableType = getCustom('table_type');
  const upstream: string[] = Array.isArray(getCustom('upstream_tables')) ? getCustom('upstream_tables') : [];
  const downstream: string[] = Array.isArray(getCustom('downstream_tables')) ? getCustom('downstream_tables') : [];
  const approverGroup = getCustom('approver_group');
  const ownerGroup = getCustom('owner_group');

  const physicalName = tbl.physicalName || tbl.name;
  const tableHref = catalogExplorerUrl(
    workspaceUrl || '',
    catalog,
    schema,
    physicalName,
  );

  return (
    <div className="border border-gray-200 rounded-lg bg-white overflow-hidden">
      <div className="w-full flex items-start gap-2 hover:bg-gray-50 transition-colors">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="flex-1 text-left px-3 py-2.5 min-w-0"
          aria-expanded={open}
        >
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                {open ? <ChevronUp className="w-4 h-4 text-gray-400 shrink-0" /> : <ChevronDown className="w-4 h-4 text-gray-400 shrink-0" />}
                <span className="text-sm font-semibold text-gray-900 truncate">
                  {tbl.businessName || tbl.name || tbl.physicalName}
                </span>
                {classification && (
                  <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-rose-50 text-rose-700 border border-rose-100 uppercase">
                    {classification}
                  </span>
                )}
              </div>
              <div className="font-mono text-xs text-gray-500 truncate ml-6">{physicalName}</div>
            </div>
            <div className="flex items-center gap-1.5 shrink-0">
              {(tableType || tbl.physicalType) && (
                <span className="px-2 py-0.5 rounded text-[11px] font-medium bg-gray-100 text-gray-700 border border-gray-200 capitalize">
                  {String(tableType || tbl.physicalType).toLowerCase()}
                </span>
              )}
              {cols.length > 0 && (
                <span className="px-2 py-0.5 rounded text-[11px] font-medium bg-blue-50 text-blue-700 border border-blue-100 inline-flex items-center gap-1">
                  <Columns3 className="w-3 h-3" /> {cols.length}
                </span>
              )}
            </div>
          </div>
        </button>
        {tableHref && (
          <a
            href={tableHref}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            className="self-center mr-2 p-1.5 rounded text-gray-400 hover:text-primary hover:bg-white border border-transparent hover:border-gray-200 transition-colors shrink-0"
            title="Open table in Catalog Explorer"
            aria-label="Open table in Catalog Explorer"
          >
            <ExternalLink className="w-3.5 h-3.5" />
          </a>
        )}
      </div>

      {open && (
        <div className="px-3 pb-3 pt-1 border-t border-gray-100 space-y-3 animate-in fade-in slide-in-from-top-1 duration-150">
          {tbl.description && (
            <p className="text-xs text-gray-600 leading-relaxed">{tbl.description}</p>
          )}

          {/* Access groups */}
          {(approverGroup || ownerGroup) && (
            <div className="flex flex-wrap gap-2 text-[11px]">
              {ownerGroup && (
                <span className="inline-flex items-center px-2 py-0.5 rounded font-medium bg-gray-50 text-gray-700 border border-gray-200">
                  <Users className="w-3 h-3 mr-1" /> Owner: <span className="ml-1 font-mono">{ownerGroup}</span>
                </span>
              )}
              {approverGroup && (
                <span className="inline-flex items-center px-2 py-0.5 rounded font-medium bg-gray-50 text-gray-700 border border-gray-200">
                  <Key className="w-3 h-3 mr-1" /> Approver: <span className="ml-1 font-mono">{approverGroup}</span>
                </span>
              )}
            </div>
          )}

          {/* Lineage */}
          {(upstream.length > 0 || downstream.length > 0) && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
              {upstream.length > 0 && (
                <div className="rounded-md border border-gray-200 bg-gray-50 p-2.5">
                  <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-gray-500 mb-1.5">
                    <ArrowUpFromLine className="w-3 h-3" /> Upstream ({upstream.length})
                  </div>
                  <ul className="space-y-0.5">
                    {upstream.map((t) => (
                      <li key={t} className="font-mono text-[11px] text-gray-700 truncate" title={t}>{t}</li>
                    ))}
                  </ul>
                </div>
              )}
              {downstream.length > 0 && (
                <div className="rounded-md border border-gray-200 bg-gray-50 p-2.5">
                  <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-gray-500 mb-1.5">
                    <ArrowDownToLine className="w-3 h-3" /> Downstream ({downstream.length})
                  </div>
                  <ul className="space-y-0.5">
                    {downstream.map((t) => (
                      <li key={t} className="font-mono text-[11px] text-gray-700 truncate" title={t}>{t}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}

          {/* Columns */}
          {cols.length > 0 && (
            <div className="rounded-md border border-gray-200 overflow-hidden">
              <div className="bg-gray-50 px-3 py-2 border-b border-gray-200 text-[11px] font-semibold uppercase tracking-wide text-gray-500 flex items-center gap-1.5">
                <Columns3 className="w-3 h-3" /> Columns
              </div>
              <table className="w-full text-xs">
                <thead className="bg-white text-gray-500 border-b border-gray-100">
                  <tr>
                    <th className="text-left font-medium px-3 py-1.5">Name</th>
                    <th className="text-left font-medium px-3 py-1.5">Type</th>
                    <th className="text-left font-medium px-3 py-1.5">Description</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {cols.map((c: any) => {
                    const colTags: string[] = Array.isArray(c.tags) ? c.tags : [];
                    return (
                      <tr key={c.id || c.name} className="hover:bg-gray-50/60">
                        <td className="px-3 py-1.5 align-top">
                          <div className="flex items-center gap-1 font-mono text-gray-900 break-all">
                            {(c.primaryKey || /pk/i.test(String(c.role || ''))) && (
                              <Key className="w-3 h-3 text-amber-500 shrink-0" aria-label="Primary key" />
                            )}
                            {c.name}
                          </div>
                          {c.businessName && c.businessName !== c.name && (
                            <div className="text-[10px] text-gray-400">{c.businessName}</div>
                          )}
                        </td>
                        <td className="px-3 py-1.5 align-top text-gray-600 font-mono whitespace-nowrap">
                          {c.logicalType || c.physicalType || '—'}
                          {c.required === true && (
                            <span className="ml-1 text-rose-500" title="Required">*</span>
                          )}
                        </td>
                        <td className="px-3 py-1.5 align-top text-gray-600 leading-snug">
                          {c.description || <span className="text-gray-400 italic">—</span>}
                          {colTags.length > 0 && (
                            <div className="flex flex-wrap gap-1 mt-1">
                              {colTags.map((t: string) => (
                                <span key={t} className="inline-block px-1.5 py-0.5 rounded text-[10px] font-mono bg-gray-100 text-gray-600 border border-gray-200">{t}</span>
                              ))}
                            </div>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {/* Tags */}
          {tags.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {tags.map((t: string) => (
                <span key={t} className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-gray-50 text-gray-600 border border-gray-200 font-mono">
                  {t}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function SectionHeading({
  icon,
  title,
  count,
}: {
  icon?: ReactNode;
  title: string;
  count?: number;
}) {
  return (
    <h3 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-gray-700 mb-3">
      {icon && <span className="text-gray-400">{icon}</span>}
      {title}
      {typeof count === 'number' && (
        <span className="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-gray-100 text-gray-600 border border-gray-200">
          {count}
        </span>
      )}
    </h3>
  );
}

function MetaRow({ label, value, mono, children }: { label: string; value?: any; mono?: boolean; children?: ReactNode }) {
  return (
    <div className="flex justify-between items-center gap-3 py-2 px-3 bg-gray-50 rounded-lg border border-gray-100">
      <span className="text-xs font-medium text-gray-500 uppercase tracking-wide">{label}</span>
      {children ? children : (
        <span className={`text-sm font-medium text-gray-900 truncate ${mono ? 'font-mono text-xs' : ''}`} title={value ? String(value) : undefined}>
          {value || <span className="text-gray-400 italic">Not set</span>}
        </span>
      )}
    </div>
  );
}

function formatDate(value: string | Date | null | undefined): string {
  if (!value) return '—';
  try {
    const d = typeof value === 'string' ? new Date(value) : value;
    if (isNaN(d.getTime())) return String(value);
    return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
  } catch {
    return String(value);
  }
}
