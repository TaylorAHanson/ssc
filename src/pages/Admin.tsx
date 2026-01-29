import { useState, useEffect } from 'react';
import { useRequestStore } from '../stores/requestStore';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Textarea } from '../components/ui/textarea';
import { Input } from '../components/ui/input';
import {
  Save, Loader2, Clock, RotateCcw, FileText,
  Activity, CheckCircle2, FileStack, TrendingUp, ToggleLeft, Search,
  ChevronUp, ChevronDown
} from 'lucide-react';
import { format, subDays, isAfter } from 'date-fns';
import {
  listContent, getContent, saveContent, getContentVersions,
  listWorkspaces, getWorkspaceFeatures, updateWorkspaceFeature
} from '../services/api';
import type { ContentInfo, ContentVersionInfo, WorkspaceInfo, FeatureInfo } from '../services/api';
import { Switch } from '../components/ui/switch';
import { TestRunner } from '../components/admin/TestRunner';
import { Users } from './admin/Users';

export function Admin() {
  const requests = useRequestStore((state) => state.requests);
  const getPendingApprovalsCount = useRequestStore((state) => state.getPendingApprovalsCount);
  const fetchRequests = useRequestStore((state) => state.fetchRequests);
  const fetchApprovals = useRequestStore((state) => state.fetchApprovals);

  useEffect(() => {
    fetchRequests();
    fetchApprovals();
  }, [fetchRequests, fetchApprovals]);

  const [activeTab, setActiveTab] = useState<'dashboard' | 'users' | 'content-manager' | 'feature-management' | 'test-runner'>('dashboard');

  // Content management state
  const [contentFiles, setContentFiles] = useState<ContentInfo[]>([]);
  const [selectedContent, setSelectedContent] = useState<string | null>(null);
  const [contentData, setContentData] = useState<string>(''); // JSON string
  const [contentVersions, setContentVersions] = useState<Record<string, ContentVersionInfo[]>>({});
  const [expandedContent, setExpandedContent] = useState<Set<string>>(new Set());
  const [isLoadingContent, setIsLoadingContent] = useState(false);
  const [loadingContentVersions, setLoadingContentVersions] = useState<Set<string>>(new Set());

  const [isSaving, setIsSaving] = useState(false);
  const [saveMessage, setSaveMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  // Feature management state
  const [workspaces, setWorkspaces] = useState<WorkspaceInfo[]>([]);
  const [selectedWorkspace, setSelectedWorkspace] = useState<string | null>(null);
  const [workspaceFeatures, setWorkspaceFeatures] = useState<FeatureInfo[]>([]);
  const [isLoadingWorkspaces, setIsLoadingWorkspaces] = useState(false);
  const [isLoadingFeatures, setIsLoadingFeatures] = useState(false);
  const [updatingFeatures, setUpdatingFeatures] = useState<Set<string>>(new Set());
  const [workspaceSearchQuery, setWorkspaceSearchQuery] = useState('');

  // Dashboard requests search and sort state
  const [dashboardSearchQuery, setDashboardSearchQuery] = useState('');
  const [sortConfig, setSortConfig] = useState<{ key: string; direction: 'asc' | 'desc' } | null>({
    key: 'createdAt',
    direction: 'desc'
  });

  // Load content on tab change
  useEffect(() => {
    if (activeTab === 'content-manager') {
      loadContentFiles();
    } else if (activeTab === 'feature-management') {
      loadWorkspaces();
    }
  }, [activeTab]);

  // Load features when workspace is selected
  useEffect(() => {
    if (selectedWorkspace && activeTab === 'feature-management') {
      loadWorkspaceFeatures(selectedWorkspace);
    }
  }, [selectedWorkspace, activeTab]);

  // Load selected content
  useEffect(() => {
    if (selectedContent) {
      loadContent(selectedContent);
    }
  }, [selectedContent]);

  const loadContentFiles = async () => {
    setIsLoadingContent(true);
    try {
      const files = await listContent();
      setContentFiles(files);
      if (files.length > 0 && !selectedContent) {
        setSelectedContent(files[0].filename);
      }
    } catch (error) {
      console.error('Failed to load content files:', error);
    } finally {
      setIsLoadingContent(false);
    }
  };

  const loadContent = async (filename: string) => {
    try {
      const data = await getContent(filename);
      setContentData(JSON.stringify(data, null, 2));
      setSaveMessage(null);
    } catch (error) {
      console.error('Failed to load content:', error);
      setSaveMessage({ type: 'error', text: `Failed to load content: ${error instanceof Error ? error.message : 'Unknown error'}` });
    }
  };

  const handleContentSelect = (filename: string) => {
    setSelectedContent(filename);
  };

  const handleContentSave = async () => {
    if (!selectedContent) return;

    setIsSaving(true);
    setSaveMessage(null);

    try {
      let content;
      try {
        content = JSON.parse(contentData);
      } catch (e) {
        throw new Error('Invalid JSON format');
      }

      await saveContent(selectedContent, content, true);
      setSaveMessage({ type: 'success', text: 'Content saved successfully!' });

      // If this is the system banner, refresh the banner message
      if (selectedContent === 'system-banner.json') {
        const fetchBannerMessage = useRequestStore.getState().fetchBannerMessage;
        await fetchBannerMessage();
      }

      // Reload versions if they're currently loaded
      if (selectedContent && contentVersions[selectedContent]) {
        await loadContentVersionList(selectedContent);
      }

      setTimeout(() => setSaveMessage(null), 3000);
    } catch (error) {
      setSaveMessage({
        type: 'error',
        text: `Failed to save content: ${error instanceof Error ? error.message : 'Unknown error'}`
      });
    } finally {
      setIsSaving(false);
    }
  };

  const toggleContentVersions = async (filename: string) => {
    const isExpanded = expandedContent.has(filename);

    if (isExpanded) {
      setExpandedContent(prev => {
        const next = new Set(prev);
        next.delete(filename);
        return next;
      });
    } else {
      setExpandedContent(prev => new Set(prev).add(filename));

      if (!contentVersions[filename]) {
        await loadContentVersionList(filename);
      }
    }
  };

  const loadContentVersionList = async (filename: string) => {
    if (loadingContentVersions.has(filename)) return;

    setLoadingContentVersions(prev => new Set(prev).add(filename));
    try {
      const versions = await getContentVersions(filename);
      setContentVersions(prev => ({ ...prev, [filename]: versions }));
    } catch (error) {
      console.error('Failed to load content versions:', error);
      setContentVersions(prev => ({ ...prev, [filename]: [] }));
    } finally {
      setLoadingContentVersions(prev => {
        const next = new Set(prev);
        next.delete(filename);
        return next;
      });
    }
  };

  const handleLoadContentVersion = async (filename: string, versionFilename: string) => {
    try {
      const data = await getContent(filename, versionFilename);
      setContentData(JSON.stringify(data, null, 2));
      setSelectedContent(filename);
      setSaveMessage({ type: 'success', text: 'Version loaded. Click Save to make it active.' });
    } catch (error) {
      console.error('Failed to load content version:', error);
      setSaveMessage({ type: 'error', text: `Failed to load version: ${error instanceof Error ? error.message : 'Unknown error'}` });
    }
  };

  const handleRevertContentVersion = async (filename: string, versionFilename: string) => {
    try {
      // Load the version
      const data = await getContent(filename, versionFilename);

      // Save it as the active version
      await saveContent(filename, data, true);

      // Update the editor
      setContentData(JSON.stringify(data, null, 2));
      setSelectedContent(filename);

      // Reload versions
      await loadContentVersionList(filename);

      setSaveMessage({ type: 'success', text: 'Version reverted successfully!' });
      setTimeout(() => setSaveMessage(null), 3000);
    } catch (error) {
      console.error('Failed to revert content version:', error);
      setSaveMessage({ type: 'error', text: `Failed to revert version: ${error instanceof Error ? error.message : 'Unknown error'}` });
    }
  };

  // Feature Management Handlers
  const loadWorkspaces = async () => {
    setIsLoadingWorkspaces(true);
    try {
      const wsList = await listWorkspaces();
      setWorkspaces(wsList);
      if (wsList.length > 0 && !selectedWorkspace) {
        setSelectedWorkspace(wsList[0].id);
      }
    } catch (error) {
      console.error('Failed to load workspaces:', error);
      setSaveMessage({ type: 'error', text: `Failed to load workspaces: ${error instanceof Error ? error.message : 'Unknown error'}` });
    } finally {
      setIsLoadingWorkspaces(false);
    }
  };

  const loadWorkspaceFeatures = async (workspaceId: string) => {
    setIsLoadingFeatures(true);
    try {
      const response = await getWorkspaceFeatures(workspaceId);
      setWorkspaceFeatures(response.features);
      setSaveMessage(null);
    } catch (error) {
      console.error('Failed to load workspace features:', error);
      setSaveMessage({ type: 'error', text: `Failed to load features: ${error instanceof Error ? error.message : 'Unknown error'}` });
    } finally {
      setIsLoadingFeatures(false);
    }
  };

  const handleFeatureToggle = async (featureId: string, enabled: boolean) => {
    if (!selectedWorkspace) return;

    setUpdatingFeatures(prev => new Set(prev).add(featureId));
    try {
      await updateWorkspaceFeature(selectedWorkspace, featureId, enabled);
      // Update local state
      setWorkspaceFeatures(prev =>
        prev.map(f => f.id === featureId ? { ...f, enabled } : f)
      );
      setSaveMessage({ type: 'success', text: 'Feature updated successfully!' });
      setTimeout(() => setSaveMessage(null), 3000);
    } catch (error) {
      console.error('Failed to update feature:', error);
      setSaveMessage({ type: 'error', text: `Failed to update feature: ${error instanceof Error ? error.message : 'Unknown error'}` });
      // Revert the toggle on error
      setWorkspaceFeatures(prev =>
        prev.map(f => f.id === featureId ? { ...f, enabled: !enabled } : f)
      );
    } finally {
      setUpdatingFeatures(prev => {
        const next = new Set(prev);
        next.delete(featureId);
        return next;
      });
    }
  };

  const handleWorkspaceSelect = (workspaceId: string) => {
    setSelectedWorkspace(workspaceId);
  };

  const handleSort = (key: string) => {
    let direction: 'asc' | 'desc' = 'asc';
    if (sortConfig && sortConfig.key === key && sortConfig.direction === 'asc') {
      direction = 'desc';
    }
    setSortConfig({ key, direction });
  };

  const filteredRequests = requests.filter(request => {
    const query = dashboardSearchQuery.toLowerCase();
    return (
      request.id.toLowerCase().includes(query) ||
      request.title.toLowerCase().includes(query) ||
      request.type.toLowerCase().includes(query) ||
      request.status.toLowerCase().includes(query) ||
      (request.requester_email || '').toLowerCase().includes(query) ||
      (request.metadata?.requested_by || '').toLowerCase().includes(query)
    );
  });

  const sortedRequests = [...filteredRequests].sort((a, b) => {
    if (!sortConfig) return 0;

    const { key, direction } = sortConfig;
    let aValue: any = a[key as keyof typeof a];
    let bValue: any = b[key as keyof typeof b];

    // Handle dates
    if (key === 'createdAt' || key === 'updatedAt') {
      aValue = new Date(aValue).getTime();
      bValue = new Date(bValue).getTime();
    }

    if (aValue < bValue) return direction === 'asc' ? -1 : 1;
    if (aValue > bValue) return direction === 'asc' ? 1 : -1;
    return 0;
  });

  const SortIcon = ({ column }: { column: string }) => {
    if (!sortConfig || sortConfig.key !== column) {
      return <ChevronUp className="w-4 h-4 text-gray-400 opacity-0 group-hover:opacity-100 transition-opacity" />;
    }
    return sortConfig.direction === 'asc'
      ? <ChevronUp className="w-4 h-4 text-primary" />
      : <ChevronDown className="w-4 h-4 text-primary" />;
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-900 mb-2">Admin Dashboard</h1>
        <p className="text-gray-600">Manage requests, system messages, and features</p>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 border-b border-gray-200">
        <button
          onClick={() => setActiveTab('dashboard')}
          className={`px-4 py-2 font-medium text-sm transition-colors ${activeTab === 'dashboard'
            ? 'border-b-2 border-primary text-primary'
            : 'text-gray-600 hover:text-gray-900'
            }`}
        >
          Dashboard
        </button>
        <button
          onClick={() => setActiveTab('users')}
          className={`px-4 py-2 font-medium text-sm transition-colors ${activeTab === 'users'
            ? 'border-b-2 border-primary text-primary'
            : 'text-gray-600 hover:text-gray-900'
            }`}
        >
          <div className="flex items-center gap-2">
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="lucide lucide-users"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M22 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" /></svg>
            Users & Roles
          </div>
        </button>
        <button
          onClick={() => setActiveTab('content-manager')}
          className={`px-4 py-2 font-medium text-sm transition-colors ${activeTab === 'content-manager'
            ? 'border-b-2 border-primary text-primary'
            : 'text-gray-600 hover:text-gray-900'
            }`}
        >
          <FileText className="w-4 h-4 inline mr-2" />
          Content Manager
        </button>
        <button
          onClick={() => setActiveTab('feature-management')}
          className={`px-4 py-2 font-medium text-sm transition-colors ${activeTab === 'feature-management'
            ? 'border-b-2 border-primary text-primary'
            : 'text-gray-600 hover:text-gray-900'
            }`}
        >
          <ToggleLeft className="w-4 h-4 inline mr-2" />
          Feature Management
        </button>
        <button
          onClick={() => setActiveTab('test-runner')}
          className={`px-4 py-2 font-medium text-sm transition-colors ${activeTab === 'test-runner'
            ? 'border-b-2 border-primary text-primary'
            : 'text-gray-600 hover:text-gray-900'
            }`}
        >
          <Activity className="w-4 h-4 inline mr-2" />
          Test Runner
        </button>
      </div>

      {activeTab === 'test-runner' && <TestRunner />}
      {activeTab === 'users' && <Users />}

      {activeTab === 'dashboard' && (
        <div className="space-y-6">
          {/* Metrics Grid */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            <Card className="bg-blue-50 border-blue-100">
              <CardContent className="p-6">
                <div className="flex items-center justify-between mb-4">
                  <div className="p-2 bg-blue-100 rounded-lg">
                    <FileStack className="w-6 h-6 text-blue-700" />
                  </div>
                  <span className="text-sm font-medium text-blue-600 bg-blue-100 px-2 py-1 rounded-full">Total</span>
                </div>
                <div className="space-y-1">
                  <h3 className="text-2xl font-bold text-gray-900">{requests.length}</h3>
                  <p className="text-sm text-gray-600">Total Requests</p>
                </div>
              </CardContent>
            </Card>

            <Card className="bg-amber-50 border-amber-100">
              <CardContent className="p-6">
                <div className="flex items-center justify-between mb-4">
                  <div className="p-2 bg-amber-100 rounded-lg">
                    <Clock className="w-6 h-6 text-amber-700" />
                  </div>
                  <span className="text-sm font-medium text-amber-600 bg-amber-100 px-2 py-1 rounded-full">Action Required</span>
                </div>
                <div className="space-y-1">
                  <h3 className="text-2xl font-bold text-gray-900">{getPendingApprovalsCount()}</h3>
                  <p className="text-sm text-gray-600">Pending Approvals</p>
                </div>
              </CardContent>
            </Card>

            <Card className="bg-green-50 border-green-100">
              <CardContent className="p-6">
                <div className="flex items-center justify-between mb-4">
                  <div className="p-2 bg-green-100 rounded-lg">
                    <CheckCircle2 className="w-6 h-6 text-green-700" />
                  </div>
                  <span className="text-sm font-medium text-green-600 bg-green-100 px-2 py-1 rounded-full">Active</span>
                </div>
                <div className="space-y-1">
                  <h3 className="text-2xl font-bold text-gray-900">{requests.filter(r => r.type === 'workspace_provision' && r.status === 'completed').length}</h3>
                  <p className="text-sm text-gray-600">Active Workspaces</p>
                </div>
              </CardContent>
            </Card>

            <Card className="bg-purple-50 border-purple-100">
              <CardContent className="p-6">
                <div className="flex items-center justify-between mb-4">
                  <div className="p-2 bg-purple-100 rounded-lg">
                    <TrendingUp className="w-6 h-6 text-purple-700" />
                  </div>
                  <span className="text-sm font-medium text-purple-600 bg-purple-100 px-2 py-1 rounded-full">Last 24h</span>
                </div>
                <div className="space-y-1">
                  <h3 className="text-2xl font-bold text-gray-900">{requests.filter(r => isAfter(new Date(r.createdAt), subDays(new Date(), 1))).length}</h3>
                  <p className="text-sm text-gray-600">New Requests</p>
                </div>
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                <CardTitle className="flex items-center gap-2">
                  <Activity className="w-5 h-5" />
                  All Requests
                </CardTitle>
                <div className="relative w-full md:w-64">
                  <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-400 w-4 h-4" />
                  <Input
                    type="text"
                    placeholder="Search requests..."
                    value={dashboardSearchQuery}
                    onChange={(e) => setDashboardSearchQuery(e.target.value)}
                    className="pl-9"
                  />
                </div>
              </div>
            </CardHeader>
            <CardContent>
              {sortedRequests.length === 0 ? (
                <div className="py-12 text-center">
                  <p className="text-gray-500">
                    {dashboardSearchQuery ? "No results matching your search" : "No requests found"}
                  </p>
                  {dashboardSearchQuery && (
                    <Button
                      variant="ghost"
                      onClick={() => setDashboardSearchQuery('')}
                      className="mt-2 text-primary"
                    >
                      Clear Search
                    </Button>
                  )}
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full">
                    <thead>
                      <tr className="border-b border-gray-200">
                        <th
                          className="text-left py-3 px-4 text-sm font-semibold text-gray-700 cursor-pointer hover:bg-gray-50 group"
                          onClick={() => handleSort('id')}
                        >
                          <div className="flex items-center gap-1">
                            ID <SortIcon column="id" />
                          </div>
                        </th>
                        <th
                          className="text-left py-3 px-4 text-sm font-semibold text-gray-700 cursor-pointer hover:bg-gray-50 group"
                          onClick={() => handleSort('title')}
                        >
                          <div className="flex items-center gap-1">
                            Title <SortIcon column="title" />
                          </div>
                        </th>
                        <th
                          className="text-left py-3 px-4 text-sm font-semibold text-gray-700 cursor-pointer hover:bg-gray-50 group"
                          onClick={() => handleSort('type')}
                        >
                          <div className="flex items-center gap-1">
                            Type <SortIcon column="type" />
                          </div>
                        </th>
                        <th
                          className="text-left py-3 px-4 text-sm font-semibold text-gray-700 cursor-pointer hover:bg-gray-50 group"
                          onClick={() => handleSort('status')}
                        >
                          <div className="flex items-center gap-1">
                            Status <SortIcon column="status" />
                          </div>
                        </th>
                        <th
                          className="text-left py-3 px-4 text-sm font-semibold text-gray-700 cursor-pointer hover:bg-gray-50 group"
                          onClick={() => handleSort('requester_email')}
                        >
                          <div className="flex items-center gap-1">
                            Requested By <SortIcon column="requester_email" />
                          </div>
                        </th>
                        <th
                          className="text-left py-3 px-4 text-sm font-semibold text-gray-700 cursor-pointer hover:bg-gray-50 group"
                          onClick={() => handleSort('createdAt')}
                        >
                          <div className="flex items-center gap-1">
                            Created <SortIcon column="createdAt" />
                          </div>
                        </th>
                        <th
                          className="text-left py-3 px-4 text-sm font-semibold text-gray-700 cursor-pointer hover:bg-gray-50 group"
                          onClick={() => handleSort('updatedAt')}
                        >
                          <div className="flex items-center gap-1">
                            Updated <SortIcon column="updatedAt" />
                          </div>
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {sortedRequests.map((request) => (
                        <tr key={request.id} className="border-b border-gray-100 hover:bg-gray-50">
                          <td className="py-3 px-4 text-sm text-gray-600 font-mono">
                            {request.id.slice(0, 8)}...
                          </td>
                          <td className="py-3 px-4 text-sm text-gray-900">{request.title}</td>
                          <td className="py-3 px-4 text-sm text-gray-600">
                            {request.type.replace(/_/g, ' ')}
                          </td>
                          <td className="py-3 px-4">
                            <span className={`px-2 py-1 rounded-full text-xs font-medium ${request.status === 'completed' ? 'bg-green-100 text-green-800' :
                              request.status === 'provisioning' ? 'bg-blue-100 text-blue-800' :
                                'bg-gray-100 text-gray-800'
                              }`}>
                              {request.status.replace(/_/g, ' ')}
                            </span>
                          </td>
                          <td className="py-3 px-4 text-sm text-gray-600">
                            {request.metadata?.requested_by || request.requester_email || '—'}
                          </td>
                          <td className="py-3 px-4 text-sm text-gray-600">
                            {format(new Date(request.createdAt), 'MMM d, yyyy HH:mm')}
                          </td>
                          <td className="py-3 px-4 text-sm text-gray-600">
                            {format(new Date(request.updatedAt), 'MMM d, yyyy HH:mm')}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      {activeTab === 'content-manager' && (
        <div className="flex gap-6 h-[calc(100vh-200px)]">
          {/* Content Files Sidebar */}
          <div className="w-64 flex-shrink-0">
            <Card className="h-full flex flex-col">
              <CardHeader>
                <CardTitle className="text-lg">Content Files</CardTitle>
              </CardHeader>
              <CardContent className="flex-1 overflow-y-auto">
                {isLoadingContent ? (
                  <div className="flex items-center justify-center py-8">
                    <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
                  </div>
                ) : contentFiles.length === 0 ? (
                  <p className="text-sm text-gray-500 text-center py-4">No content files found</p>
                ) : (
                  <div className="space-y-1">
                    {contentFiles.map((file) => {
                      const isExpanded = expandedContent.has(file.filename);
                      const versions = contentVersions[file.filename];
                      const isLoading = loadingContentVersions.has(file.filename);
                      const hasVersionsLoaded = contentVersions[file.filename] !== undefined;

                      return (
                        <div key={file.filename}>
                          <div className="flex items-center">
                            <button
                              onClick={() => handleContentSelect(file.filename)}
                              className={`flex-1 text-left px-3 py-2 rounded-md text-sm transition-colors ${selectedContent === file.filename
                                ? 'bg-primary text-white'
                                : 'hover:bg-gray-100 text-gray-700'
                                }`}
                            >
                              {file.title}
                            </button>
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                toggleContentVersions(file.filename);
                              }}
                              className={`px-2 py-2 rounded-md transition-colors ${isExpanded
                                ? 'text-primary bg-primary/10 hover:bg-primary/20'
                                : 'text-gray-500 hover:text-gray-700 hover:bg-gray-100'
                                }`}
                              title={isExpanded ? "Hide versions" : "Show versions"}
                            >
                              <Clock className="w-4 h-4" />
                            </button>
                          </div>

                          {/* Expanded versions list */}
                          {isExpanded && (
                            <div className="ml-4 mt-1 mb-2 space-y-1 border-l-2 border-gray-200 pl-2">
                              {isLoading ? (
                                <div className="flex items-center justify-center py-2">
                                  <Loader2 className="w-3 h-3 animate-spin text-gray-400" />
                                </div>
                              ) : !hasVersionsLoaded ? (
                                <div className="flex items-center justify-center py-2">
                                  <Loader2 className="w-3 h-3 animate-spin text-gray-400" />
                                </div>
                              ) : !versions || versions.length === 0 ? (
                                <p className="text-xs text-gray-500 py-2">No versions found</p>
                              ) : (
                                versions.map((version) => (
                                  <div
                                    key={version.filename}
                                    className={`p-2 rounded-md text-xs ${version.is_active
                                      ? 'bg-green-50 border border-green-200'
                                      : 'bg-gray-50 border border-gray-200'
                                      }`}
                                  >
                                    <div className="flex items-center justify-between mb-1">
                                      <span className="font-medium">
                                        {version.is_active ? (
                                          <span className="text-green-700">Active</span>
                                        ) : (
                                          format(new Date(version.date), 'MMM d, yyyy')
                                        )}
                                      </span>
                                      {!version.is_active && (
                                        <div className="flex gap-1">
                                          <button
                                            onClick={() => handleLoadContentVersion(file.filename, version.filename)}
                                            className="text-primary hover:underline text-xs px-1"
                                            title="Load version"
                                          >
                                            Load
                                          </button>
                                          <button
                                            onClick={() => handleRevertContentVersion(file.filename, version.filename)}
                                            className="text-orange-600 hover:underline text-xs px-1 flex items-center gap-1"
                                            title="Revert to this version"
                                          >
                                            <RotateCcw className="w-3 h-3" />
                                            Revert
                                          </button>
                                        </div>
                                      )}
                                    </div>
                                    <div className="text-gray-500">
                                      {format(new Date(version.date), 'MMM d, yyyy HH:mm')}
                                    </div>
                                  </div>
                                ))
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </CardContent>
            </Card>
          </div>

          {/* Content Editor */}
          <div className="flex-1 min-w-0">
            <Card className="h-full flex flex-col">
              <CardHeader>
                <div className="flex items-center justify-between">
                  <CardTitle>
                    {selectedContent
                      ? contentFiles.find(f => f.filename === selectedContent)?.title || 'Content Editor'
                      : 'Content Editor'}
                  </CardTitle>
                  {selectedContent && (
                    <Button
                      onClick={handleContentSave}
                      disabled={isSaving}
                      className="flex items-center gap-2"
                    >
                      {isSaving ? (
                        <>
                          <Loader2 className="w-4 h-4 animate-spin" />
                          Saving...
                        </>
                      ) : (
                        <>
                          <Save className="w-4 h-4" />
                          Save Content
                        </>
                      )}
                    </Button>
                  )}
                </div>
              </CardHeader>
              <CardContent className="flex-1 overflow-hidden flex flex-col">
                {saveMessage && (
                  <div className={`mb-4 p-3 rounded-md flex-shrink-0 ${saveMessage.type === 'success'
                    ? 'bg-green-50 border border-green-200 text-green-800'
                    : 'bg-red-50 border border-red-200 text-red-800'
                    }`}>
                    <p className="text-sm">{saveMessage.text}</p>
                  </div>
                )}

                {!selectedContent ? (
                  <div className="py-12 text-center">
                    <p className="text-gray-500">Select a file from the sidebar to edit</p>
                  </div>
                ) : (
                  <>
                    <div className="mb-4 p-4 bg-blue-50 border border-blue-200 rounded-md flex-shrink-0">
                      <p className="text-sm text-blue-800">
                        Editing: <strong>{selectedContent}</strong>.
                        {selectedContent === 'system-banner.json'
                          ? ' This file controls the global banner message displayed at the top of the application. Set "active" to true to show the message, false to hide it. The "type" field controls the banner styling: "info" (blue, default), "alert" (red), "warning" (yellow), or "success" (green).'
                          : ' This content is used by the AI agent and the community pages.'}
                      </p>
                    </div>
                    <div className="flex-1 min-h-0 border border-gray-200 rounded-lg overflow-hidden">
                      <Textarea
                        value={contentData}
                        onChange={(e) => setContentData(e.target.value)}
                        className="w-full h-full font-mono text-sm p-4 resize-none border-0 focus-visible:ring-0"
                        spellCheck={false}
                      />
                    </div>
                  </>
                )}
              </CardContent>
            </Card>
          </div>
        </div>
      )}

      {activeTab === 'feature-management' && (
        <div className="flex gap-6 h-[calc(100vh-200px)]">
          {/* Workspaces List Sidebar */}
          <div className="w-64 flex-shrink-0">
            <Card className="h-full flex flex-col">
              <CardHeader>
                <CardTitle className="text-lg">Workspaces</CardTitle>
              </CardHeader>
              <CardContent className="flex-1 overflow-hidden flex flex-col">
                {/* Search Input */}
                <div className="relative mb-4 flex-shrink-0">
                  <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-400 w-4 h-4" />
                  <Input
                    type="text"
                    placeholder="Search workspaces..."
                    value={workspaceSearchQuery}
                    onChange={(e) => setWorkspaceSearchQuery(e.target.value)}
                    className="pl-9"
                  />
                </div>

                {/* Workspaces List */}
                <div className="flex-1 overflow-y-auto">
                  {isLoadingWorkspaces ? (
                    <div className="flex items-center justify-center py-8">
                      <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
                    </div>
                  ) : (() => {
                    const filteredWorkspaces = workspaces.filter(ws =>
                      ws.name.toLowerCase().includes(workspaceSearchQuery.toLowerCase()) ||
                      ws.id.toLowerCase().includes(workspaceSearchQuery.toLowerCase())
                    );

                    if (filteredWorkspaces.length === 0) {
                      return <p className="text-sm text-gray-500 text-center py-4">No workspaces found</p>;
                    }

                    return (
                      <div className="space-y-1">
                        {filteredWorkspaces.map((workspace) => (
                          <button
                            key={workspace.id}
                            onClick={() => handleWorkspaceSelect(workspace.id)}
                            title={workspace.url || undefined}
                            className={`w-full text-left px-3 py-2 rounded-md text-sm transition-colors ${selectedWorkspace === workspace.id
                              ? 'bg-primary text-white'
                              : 'hover:bg-gray-100 text-gray-700'
                              }`}
                          >
                            <div className="font-medium truncate">{workspace.name}</div>
                          </button>
                        ))}
                      </div>
                    );
                  })()}
                </div>
              </CardContent>
            </Card>
          </div>

          {/* Features Management */}
          <div className="flex-1 min-w-0">
            <Card className="h-full flex flex-col">
              <CardHeader>
                <CardTitle>
                  {selectedWorkspace
                    ? workspaces.find(w => w.id === selectedWorkspace)?.name || 'Feature Management'
                    : 'Feature Management'}
                </CardTitle>
              </CardHeader>
              <CardContent className="flex-1 overflow-y-auto">
                {saveMessage && (
                  <div className={`mb-4 p-3 rounded-md ${saveMessage.type === 'success'
                    ? 'bg-green-50 border border-green-200 text-green-800'
                    : 'bg-red-50 border border-red-200 text-red-800'
                    }`}>
                    <p className="text-sm">{saveMessage.text}</p>
                  </div>
                )}

                {!selectedWorkspace ? (
                  <div className="py-12 text-center">
                    <p className="text-gray-500">Select a workspace from the sidebar</p>
                  </div>
                ) : (
                  <div className="space-y-6">
                    {isLoadingFeatures ? (
                      <div className="flex items-center justify-center py-8">
                        <Loader2 className="w-8 h-8 animate-spin text-gray-400" />
                      </div>
                    ) : (
                      <div className="space-y-8">
                        {/* Beta Features */}
                        <div>
                          <h3 className="text-lg font-semibold text-gray-900 mb-4 flex items-center gap-2">
                            <span className="px-2 py-1 bg-amber-100 text-amber-800 rounded text-xs font-medium">BETA</span>
                            Beta Features
                          </h3>
                          <div className="space-y-4">
                            {workspaceFeatures
                              .filter(f => f.category === 'beta')
                              .map((feature) => (
                                <div
                                  key={feature.id}
                                  className="p-4 border border-gray-200 rounded-lg hover:border-gray-300 transition-colors"
                                >
                                  <div className="flex items-start justify-between">
                                    <div className="flex-1">
                                      <div className="flex items-center gap-2 mb-2">
                                        <h4 className="font-semibold text-gray-900">{feature.name}</h4>
                                      </div>
                                      <p className="text-sm text-gray-600">{feature.description}</p>
                                    </div>
                                    <div className="ml-4 flex items-center">
                                      {updatingFeatures.has(feature.id) ? (
                                        <Loader2 className="w-5 h-5 animate-spin text-gray-400" />
                                      ) : (
                                        <Switch
                                          checked={feature.enabled}
                                          onCheckedChange={(checked) => handleFeatureToggle(feature.id, checked)}
                                        />
                                      )}
                                    </div>
                                  </div>
                                </div>
                              ))}
                            {workspaceFeatures.filter(f => f.category === 'beta').length === 0 && (
                              <p className="text-sm text-gray-500 py-4">No beta features available</p>
                            )}
                          </div>
                        </div>

                        {/* Public Preview Features */}
                        <div>
                          <h3 className="text-lg font-semibold text-gray-900 mb-4 flex items-center gap-2">
                            <span className="px-2 py-1 bg-blue-100 text-blue-800 rounded text-xs font-medium">PUBLIC PREVIEW</span>
                            Public Preview Features
                          </h3>
                          <div className="space-y-4">
                            {workspaceFeatures
                              .filter(f => f.category === 'public_preview')
                              .map((feature) => (
                                <div
                                  key={feature.id}
                                  className="p-4 border border-gray-200 rounded-lg hover:border-gray-300 transition-colors"
                                >
                                  <div className="flex items-start justify-between">
                                    <div className="flex-1">
                                      <div className="flex items-center gap-2 mb-2">
                                        <h4 className="font-semibold text-gray-900">{feature.name}</h4>
                                      </div>
                                      <p className="text-sm text-gray-600">{feature.description}</p>
                                    </div>
                                    <div className="ml-4 flex items-center">
                                      {updatingFeatures.has(feature.id) ? (
                                        <Loader2 className="w-5 h-5 animate-spin text-gray-400" />
                                      ) : (
                                        <Switch
                                          checked={feature.enabled}
                                          onCheckedChange={(checked) => handleFeatureToggle(feature.id, checked)}
                                        />
                                      )}
                                    </div>
                                  </div>
                                </div>
                              ))}
                            {workspaceFeatures.filter(f => f.category === 'public_preview').length === 0 && (
                              <p className="text-sm text-gray-500 py-4">No public preview features available</p>
                            )}
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        </div>
      )}
    </div>
  );
}
