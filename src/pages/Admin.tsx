import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useRequestStore } from '../stores/requestStore';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Textarea } from '../components/ui/textarea';
import {
  Save, Loader2, Clock, RotateCcw, FileText, Activity, MessageSquarePlus, GraduationCap
} from 'lucide-react';
import { format } from 'date-fns';
import {
  listContent, getContent, saveContent, getContentVersions
} from '../services/api';
import type { ContentInfo, ContentVersionInfo } from '../services/api';
import { TestRunner } from '../components/admin/TestRunner';
import { Users } from './admin/Users';
import { AdminDashboard } from './admin/AdminDashboard';
import { FeedbackAdmin } from './admin/FeedbackAdmin';
import { TrainingUpload } from '../components/admin/TrainingUpload';
import { useBrandingStore } from '../stores/brandingStore';

export function Admin() {
  const fetchRequests = useRequestStore((state) => state.fetchRequests);
  const fetchApprovals = useRequestStore((state) => state.fetchApprovals);

  useEffect(() => {
    fetchRequests();
    fetchApprovals();
  }, [fetchRequests, fetchApprovals]);

  const { tab } = useParams<{ tab: string }>();
  const navigate = useNavigate();
  const uiTabs = useBrandingStore((s) => s.uiTabs);
  const feedbackEnabled = uiTabs?.feedback !== false;

  const validTabs = ['dashboard', 'users', 'content-manager', 'test-runner', 'training', ...(feedbackEnabled ? ['feedback'] : [])];
  const activeTab = tab && validTabs.includes(tab) ? tab as 'dashboard' | 'users' | 'content-manager' | 'test-runner' | 'training' | 'feedback' : 'dashboard';

  const handleTabChange = (newTab: string) => {
    navigate(`/admin/${newTab}`);
  };

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

  // Dashboard requests search and sort state

  // Load content on tab change
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (activeTab === 'content-manager') {
      loadContentFiles();
    }
  }, [activeTab]);

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
      } catch {
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

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-900 mb-2">Admin Dashboard</h1>
        <p className="text-gray-600">Manage requests, system messages, and features</p>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 border-b border-gray-200">
        <button
          onClick={() => handleTabChange('dashboard')}
          className={`px-4 py-2 font-medium text-sm transition-colors ${activeTab === 'dashboard'
            ? 'border-b-2 border-primary text-primary'
            : 'text-gray-600 hover:text-gray-900'
            }`}
        >
          Dashboard
        </button>
        <button
          onClick={() => handleTabChange('users')}
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
          onClick={() => handleTabChange('content-manager')}
          className={`px-4 py-2 font-medium text-sm transition-colors ${activeTab === 'content-manager'
            ? 'border-b-2 border-primary text-primary'
            : 'text-gray-600 hover:text-gray-900'
            }`}
        >
          <FileText className="w-4 h-4 inline mr-2" />
          Content Manager
        </button>
        <button
          onClick={() => handleTabChange('test-runner')}
          className={`px-4 py-2 font-medium text-sm transition-colors ${activeTab === 'test-runner'
            ? 'border-b-2 border-primary text-primary'
            : 'text-gray-600 hover:text-gray-900'
            }`}
        >
          <Activity className="w-4 h-4 inline mr-2" />
          Test Runner
        </button>
        <button
          onClick={() => handleTabChange('training')}
          className={`px-4 py-2 font-medium text-sm transition-colors ${activeTab === 'training'
            ? 'border-b-2 border-primary text-primary'
            : 'text-gray-600 hover:text-gray-900'
            }`}
        >
          <GraduationCap className="w-4 h-4 inline mr-2" />
          Training Upload
        </button>
        {feedbackEnabled && (
          <button
            onClick={() => handleTabChange('feedback')}
            className={`px-4 py-2 font-medium text-sm transition-colors ${activeTab === 'feedback'
              ? 'border-b-2 border-primary text-primary'
              : 'text-gray-600 hover:text-gray-900'
              }`}
          >
            <MessageSquarePlus className="w-4 h-4 inline mr-2" />
            Feedback
          </button>
        )}
      </div>

      {activeTab === 'test-runner' && <TestRunner />}
      {activeTab === 'dashboard' && <AdminDashboard />}
      {activeTab === 'users' && <Users />}
      {activeTab === 'training' && <TrainingUpload />}
      {activeTab === 'feedback' && <FeedbackAdmin />}

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

    </div>
  );
}
