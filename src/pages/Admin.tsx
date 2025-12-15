import { useState, useEffect, useRef } from 'react';
import { useRequestStore } from '../stores/requestStore';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Input } from '../components/ui/input';
import { Button } from '../components/ui/button';
import { X, AlertCircle, Settings, Save, Loader2, Clock, RotateCcw } from 'lucide-react';
import { format } from 'date-fns';
import { SurveyCreator, SurveyCreatorComponent } from 'survey-creator-react';
import { listForms, getForm, saveForm, getFormVersions } from '../services/api';
import type { FormInfo, FormVersionInfo } from '../services/api';
import 'survey-core/survey-core.min.css';
import 'survey-creator-core/survey-creator-core.min.css';

export function Admin() {
  const requests = useRequestStore((state) => state.requests);
  const bannerMessage = useRequestStore((state) => state.bannerMessage);
  const setBannerMessage = useRequestStore((state) => state.setBannerMessage);
  const [bannerInput, setBannerInput] = useState('');
  const [activeTab, setActiveTab] = useState<'dashboard' | 'form-designer'>('dashboard');
  const [surveyCreator, setSurveyCreator] = useState<SurveyCreator | null>(null);
  const creatorRef = useRef<SurveyCreator | null>(null);
  
  // Form management state
  const [forms, setForms] = useState<FormInfo[]>([]);
  const [selectedForm, setSelectedForm] = useState<string | null>(null);
  const [formVersions, setFormVersions] = useState<Record<string, FormVersionInfo[]>>({});
  const [expandedForms, setExpandedForms] = useState<Set<string>>(new Set());
  const [isLoadingForms, setIsLoadingForms] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [saveMessage, setSaveMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [loadingVersions, setLoadingVersions] = useState<Set<string>>(new Set());

  // Load forms list on mount and when form-designer tab is active
  useEffect(() => {
    if (activeTab === 'form-designer') {
      loadForms();
    }
  }, [activeTab]);

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

  const handleSetBanner = () => {
    setBannerMessage(bannerInput.trim() || null);
    setBannerInput('');
  };

  const handleClearBanner = () => {
    setBannerMessage(null);
    setBannerInput('');
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
      </div>

      {activeTab === 'dashboard' && (
        <>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <AlertCircle className="w-5 h-5" />
            Global Banner Message
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex gap-2">
            <Input
              placeholder="Enter banner message (e.g., system outage notice)..."
              value={bannerInput}
              onChange={(e) => setBannerInput(e.target.value)}
            />
            <Button onClick={handleSetBanner}>Set Banner</Button>
            {bannerMessage && (
              <Button variant="outline" onClick={handleClearBanner}>
                <X className="w-4 h-4 mr-2" />
                Clear
              </Button>
            )}
          </div>
          {bannerMessage && (
            <div className="p-3 bg-yellow-50 border border-yellow-200 rounded-md">
              <p className="text-sm text-yellow-800">Current: {bannerMessage}</p>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>All Requests</CardTitle>
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
        </>
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
    </div>
  );
}

