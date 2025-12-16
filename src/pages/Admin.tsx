import { useState, useEffect, useRef } from 'react';
import { useRequestStore } from '../stores/requestStore';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Textarea } from '../components/ui/textarea';
import { Input } from '../components/ui/input';
import { 
  Settings, Save, Loader2, Clock, RotateCcw, FileText, 
  Activity, CheckCircle2, FileStack, TrendingUp, ToggleLeft, Search
} from 'lucide-react';
import { format, subDays, isAfter } from 'date-fns';
import { SurveyCreator, SurveyCreatorComponent } from 'survey-creator-react';
import { 
  listForms, getForm, saveForm, getFormVersions,
  listContent, getContent, saveContent, getContentVersions,
  listWorkspaces, getWorkspaceFeatures, updateWorkspaceFeature
} from '../services/api';
import type { FormInfo, FormVersionInfo, ContentInfo, ContentVersionInfo, WorkspaceInfo, FeatureInfo } from '../services/api';
import { Switch } from '../components/ui/switch';
import 'survey-core/survey-core.min.css';
import 'survey-creator-core/survey-creator-core.min.css';

export function Admin() {
  const requests = useRequestStore((state) => state.requests);
  const getPendingApprovalsCount = useRequestStore((state) => state.getPendingApprovalsCount);
  const [activeTab, setActiveTab] = useState<'dashboard' | 'form-designer' | 'content-manager' | 'feature-management'>('dashboard');
  const [surveyCreator, setSurveyCreator] = useState<SurveyCreator | null>(null);
  const creatorRef = useRef<SurveyCreator | null>(null);
  
  // Form management state
  const [forms, setForms] = useState<FormInfo[]>([]);
  const [selectedForm, setSelectedForm] = useState<string | null>(null);
  const [formVersions, setFormVersions] = useState<Record<string, FormVersionInfo[]>>({});
  const [expandedForms, setExpandedForms] = useState<Set<string>>(new Set());
  const [isLoadingForms, setIsLoadingForms] = useState(false);
  
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
  const [loadingVersions, setLoadingVersions] = useState<Set<string>>(new Set());
  
  // Feature management state
  const [workspaces, setWorkspaces] = useState<WorkspaceInfo[]>([]);
  const [selectedWorkspace, setSelectedWorkspace] = useState<string | null>(null);
  const [workspaceFeatures, setWorkspaceFeatures] = useState<FeatureInfo[]>([]);
  const [isLoadingWorkspaces, setIsLoadingWorkspaces] = useState(false);
  const [isLoadingFeatures, setIsLoadingFeatures] = useState(false);
  const [updatingFeatures, setUpdatingFeatures] = useState<Set<string>>(new Set());
  const [workspaceSearchQuery, setWorkspaceSearchQuery] = useState('');

  // Load forms list on mount and when form-designer tab is active
  useEffect(() => {
    if (activeTab === 'form-designer') {
      loadForms();
    } else if (activeTab === 'content-manager') {
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

  // Initialize SurveyJS Creator
  useEffect(() => {
    if (activeTab === 'form-designer' && !creatorRef.current) {
      const creator = new SurveyCreator({
        showEmbededSurveyTab: false,
        isAutoSave: false, // We'll handle saving manually
      });
      creatorRef.current = creator;
      setSurveyCreator(creator);
    }
  }, [activeTab]);

  // Load selected form into creator
  useEffect(() => {
    if (selectedForm && surveyCreator) {
      loadFormIntoCreator(selectedForm);
    }
  }, [selectedForm, surveyCreator]);

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

  const loadForms = async () => {
    setIsLoadingForms(true);
    try {
      const formsList = await listForms();
      setForms(formsList);
      if (formsList.length > 0 && !selectedForm) {
        setSelectedForm(formsList[0].path);
      }
    } catch (error) {
      console.error('Failed to load forms:', error);
    } finally {
      setIsLoadingForms(false);
    }
  };

  const loadFormIntoCreator = async (formPath: string) => {
    if (!surveyCreator) return;
    
    try {
      const formData = await getForm(formPath);
      surveyCreator.JSON = formData.schema;
      setSaveMessage(null);
    } catch (error) {
      console.error('Failed to load form:', error);
      setSaveMessage({ type: 'error', text: `Failed to load form: ${error instanceof Error ? error.message : 'Unknown error'}` });
    }
  };

  const handleFormSelect = (formPath: string) => {
    setSelectedForm(formPath);
  };

  const toggleFormVersions = async (formPath: string) => {
    const isExpanded = expandedForms.has(formPath);
    console.log('toggleFormVersions called:', { formPath, isExpanded, hasVersions: !!formVersions[formPath] });
    
    if (isExpanded) {
      // Collapse
      setExpandedForms(prev => {
        const next = new Set(prev);
        next.delete(formPath);
        return next;
      });
    } else {
      // Expand - load versions if not already loaded
      setExpandedForms(prev => new Set(prev).add(formPath));
      
      if (!formVersions[formPath]) {
        console.log('Loading versions for:', formPath);
        await loadFormVersions(formPath);
      } else {
        console.log('Versions already loaded for:', formPath, formVersions[formPath]);
      }
    }
  };

  const handleSave = async () => {
    if (!selectedForm || !surveyCreator) return;

    setIsSaving(true);
    setSaveMessage(null);

    try {
      const schema = surveyCreator.JSON;
      await saveForm(selectedForm, schema, true);
      setSaveMessage({ type: 'success', text: 'Form saved successfully!' });
      
      // Reload versions if they're currently loaded
      if (selectedForm && formVersions[selectedForm]) {
        await loadFormVersions(selectedForm);
      }
      
      // Clear message after 3 seconds
      setTimeout(() => setSaveMessage(null), 3000);
    } catch (error) {
      setSaveMessage({ 
        type: 'error', 
        text: `Failed to save form: ${error instanceof Error ? error.message : 'Unknown error'}` 
      });
    } finally {
      setIsSaving(false);
    }
  };

  const loadFormVersions = async (formPath: string) => {
    if (loadingVersions.has(formPath)) return;
    
    setLoadingVersions(prev => new Set(prev).add(formPath));
    try {
      const versions = await getFormVersions(formPath);
      console.log(`Loaded ${versions.length} versions for ${formPath}:`, versions);
      setFormVersions(prev => ({ ...prev, [formPath]: versions }));
    } catch (error) {
      console.error('Failed to load form versions:', error);
      // Set empty array on error so UI shows "No versions found" instead of loading forever
      setFormVersions(prev => ({ ...prev, [formPath]: [] }));
    } finally {
      setLoadingVersions(prev => {
        const next = new Set(prev);
        next.delete(formPath);
        return next;
      });
    }
  };

  const handleLoadVersion = async (formPath: string, versionFilename: string) => {
    if (!surveyCreator) return;
    
    try {
      const formData = await getForm(formPath, versionFilename);
      surveyCreator.JSON = formData.schema;
      setSelectedForm(formPath);
      setSaveMessage({ type: 'success', text: 'Version loaded. Click Save to make it active.' });
    } catch (error) {
      console.error('Failed to load version:', error);
      setSaveMessage({ type: 'error', text: `Failed to load version: ${error instanceof Error ? error.message : 'Unknown error'}` });
    }
  };

  const handleRevertVersion = async (formPath: string, versionFilename: string) => {
    if (!surveyCreator) return;
    
    try {
      // Load the version
      const formData = await getForm(formPath, versionFilename);
      
      // Save it as the active version (this will create a new backup of current)
      await saveForm(formPath, formData.schema, true);
      
      // Update the creator
      surveyCreator.JSON = formData.schema;
      setSelectedForm(formPath);
      
      // Reload versions to show updated list
      await loadFormVersions(formPath);
      
      setSaveMessage({ type: 'success', text: 'Version reverted successfully!' });
      setTimeout(() => setSaveMessage(null), 3000);
    } catch (error) {
      console.error('Failed to revert version:', error);
      setSaveMessage({ type: 'error', text: `Failed to revert version: ${error instanceof Error ? error.message : 'Unknown error'}` });
    }
  };

  // Content Handlers
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

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-900 mb-2">Admin Dashboard</h1>
        <p className="text-gray-600">Manage requests, system messages, and intake forms</p>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 border-b border-gray-200">
        <button
          onClick={() => setActiveTab('dashboard')}
          className={`px-4 py-2 font-medium text-sm transition-colors ${
            activeTab === 'dashboard'
              ? 'border-b-2 border-primary text-primary'
              : 'text-gray-600 hover:text-gray-900'
          }`}
        >
          Dashboard
        </button>
        <button
          onClick={() => setActiveTab('form-designer')}
          className={`px-4 py-2 font-medium text-sm transition-colors ${
            activeTab === 'form-designer'
              ? 'border-b-2 border-primary text-primary'
              : 'text-gray-600 hover:text-gray-900'
          }`}
        >
          <Settings className="w-4 h-4 inline mr-2" />
          Form Designer
        </button>
        <button
          onClick={() => setActiveTab('content-manager')}
          className={`px-4 py-2 font-medium text-sm transition-colors ${
            activeTab === 'content-manager'
              ? 'border-b-2 border-primary text-primary'
              : 'text-gray-600 hover:text-gray-900'
          }`}
        >
          <FileText className="w-4 h-4 inline mr-2" />
          Content Manager
        </button>
        <button
          onClick={() => setActiveTab('feature-management')}
          className={`px-4 py-2 font-medium text-sm transition-colors ${
            activeTab === 'feature-management'
              ? 'border-b-2 border-primary text-primary'
              : 'text-gray-600 hover:text-gray-900'
          }`}
        >
          <ToggleLeft className="w-4 h-4 inline mr-2" />
          Feature Management
        </button>
      </div>

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
              <CardTitle className="flex items-center gap-2">
                <Activity className="w-5 h-5" />
                All Requests
              </CardTitle>
            </CardHeader>
        <CardContent>
          {requests.length === 0 ? (
            <p className="text-gray-500 text-center py-8">No requests found</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-gray-200">
                    <th className="text-left py-3 px-4 text-sm font-semibold text-gray-700">ID</th>
                    <th className="text-left py-3 px-4 text-sm font-semibold text-gray-700">Title</th>
                    <th className="text-left py-3 px-4 text-sm font-semibold text-gray-700">Type</th>
                    <th className="text-left py-3 px-4 text-sm font-semibold text-gray-700">Status</th>
                    <th className="text-left py-3 px-4 text-sm font-semibold text-gray-700">Created</th>
                    <th className="text-left py-3 px-4 text-sm font-semibold text-gray-700">Updated</th>
                  </tr>
                </thead>
                <tbody>
                  {requests.map((request) => (
                    <tr key={request.id} className="border-b border-gray-100 hover:bg-gray-50">
                      <td className="py-3 px-4 text-sm text-gray-600 font-mono">
                        {request.id.slice(0, 8)}...
                      </td>
                      <td className="py-3 px-4 text-sm text-gray-900">{request.title}</td>
                      <td className="py-3 px-4 text-sm text-gray-600">
                        {request.type.replace(/_/g, ' ')}
                      </td>
                      <td className="py-3 px-4">
                        <span className={`px-2 py-1 rounded-full text-xs font-medium ${
                          request.status === 'completed' ? 'bg-green-100 text-green-800' :
                          request.status === 'provisioning' ? 'bg-blue-100 text-blue-800' :
                          'bg-gray-100 text-gray-800'
                        }`}>
                          {request.status.replace(/_/g, ' ')}
                        </span>
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

      {activeTab === 'form-designer' && (
        <div className="flex gap-6 h-[calc(100vh-200px)]">
          {/* Forms List Sidebar */}
          <div className="w-64 flex-shrink-0">
            <Card className="h-full flex flex-col">
              <CardHeader>
                <CardTitle className="text-lg">Forms</CardTitle>
              </CardHeader>
              <CardContent className="flex-1 overflow-y-auto">
                {isLoadingForms ? (
                  <div className="flex items-center justify-center py-8">
                    <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
                  </div>
                ) : forms.length === 0 ? (
                  <p className="text-sm text-gray-500 text-center py-4">No forms found</p>
                ) : (
                  <div className="space-y-1">
                    {forms.map((form) => {
                      const isExpanded = expandedForms.has(form.path);
                      const versions = formVersions[form.path];
                      const isLoading = loadingVersions.has(form.path);
                      const hasVersionsLoaded = formVersions[form.path] !== undefined;
                      
                      // Debug logging
                      if (isExpanded) {
                        console.log('Rendering expanded form:', {
                          formPath: form.path,
                          isExpanded,
                          hasVersionsLoaded,
                          isLoading,
                          versionsCount: versions?.length ?? 'undefined',
                          versions: versions
                        });
                      }
                      
                      return (
                        <div key={form.path}>
                          <div className="flex items-center">
                            <button
                              onClick={() => handleFormSelect(form.path)}
                              className={`flex-1 text-left px-3 py-2 rounded-md text-sm transition-colors ${
                                selectedForm === form.path
                                  ? 'bg-primary text-white'
                                  : 'hover:bg-gray-100 text-gray-700'
                              }`}
                            >
                              {form.title}
                            </button>
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                toggleFormVersions(form.path);
                              }}
                              className={`px-2 py-2 rounded-md transition-colors ${
                                isExpanded
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
                                    className={`p-2 rounded-md text-xs ${
                                      version.is_active
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
                                            onClick={() => handleLoadVersion(form.path, version.filename)}
                                            className="text-primary hover:underline text-xs px-1"
                                            title="Load version"
                                          >
                                            Load
                                          </button>
                                          <button
                                            onClick={() => handleRevertVersion(form.path, version.filename)}
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

          {/* Form Designer - Full Width */}
          <div className="flex-1 min-w-0">
            <Card className="h-full flex flex-col">
              <CardHeader>
                <div className="flex items-center justify-between">
                  <CardTitle>
                    {selectedForm 
                      ? forms.find(f => f.path === selectedForm)?.title || 'Form Designer'
                      : 'Form Designer'}
                  </CardTitle>
                  {selectedForm && (
                    <Button 
                      onClick={handleSave} 
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
                          Save Form
                        </>
                      )}
                    </Button>
                  )}
                </div>
              </CardHeader>
              <CardContent className="flex-1 overflow-hidden flex flex-col">
                {saveMessage && (
                  <div className={`mb-4 p-3 rounded-md flex-shrink-0 ${
                    saveMessage.type === 'success'
                      ? 'bg-green-50 border border-green-200 text-green-800'
                      : 'bg-red-50 border border-red-200 text-red-800'
                  }`}>
                    <p className="text-sm">{saveMessage.text}</p>
                  </div>
                )}
                
                {!selectedForm ? (
                  <div className="py-12 text-center">
                    <p className="text-gray-500">Select a form from the sidebar to edit</p>
                  </div>
                ) : (
                  <>
                    <div className="mb-4 p-4 bg-blue-50 border border-blue-200 rounded-md flex-shrink-0">
                      <p className="text-sm text-blue-800">
                        Editing: <strong>{selectedForm}</strong>. Make your changes and click Save to update the form.
                      </p>
                    </div>
                    <div className="border border-gray-200 rounded-lg overflow-hidden flex-1 min-h-0">
                      {surveyCreator && <SurveyCreatorComponent creator={surveyCreator} />}
                    </div>
                  </>
                )}
              </CardContent>
            </Card>
          </div>
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
                              className={`flex-1 text-left px-3 py-2 rounded-md text-sm transition-colors ${
                                selectedContent === file.filename
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
                              className={`px-2 py-2 rounded-md transition-colors ${
                                isExpanded
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
                                    className={`p-2 rounded-md text-xs ${
                                      version.is_active
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
                  <div className={`mb-4 p-3 rounded-md flex-shrink-0 ${
                    saveMessage.type === 'success'
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
                            className={`w-full text-left px-3 py-2 rounded-md text-sm transition-colors ${
                              selectedWorkspace === workspace.id
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
                  <div className={`mb-4 p-3 rounded-md ${
                    saveMessage.type === 'success'
                      ? 'bg-green-50 border border-green-200 text-green-800'
                      : 'bg-red-50 border border-red-200 text-red-800'
                  }`}>
                    <p className="text-sm">{saveMessage.text}</p>
                  </div>
                )}
                
                {!selectedWorkspace ? (
                  <div className="py-12 text-center">
                    <p className="text-gray-500">Select a workspace from the sidebar to manage features</p>
                  </div>
                ) : isLoadingFeatures ? (
                  <div className="flex items-center justify-center py-12">
                    <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
                  </div>
                ) : (
                  <div className="space-y-6">
                    {/* Beta Features */}
                    <div>
                      <h3 className="text-lg font-semibold text-gray-900 mb-4 flex items-center gap-2">
                        <span className="px-2 py-1 bg-orange-100 text-orange-800 rounded text-xs font-medium">BETA</span>
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
              </CardContent>
            </Card>
          </div>
        </div>
      )}
    </div>
  );
}

