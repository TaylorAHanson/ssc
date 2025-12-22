import { useEffect } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Survey } from 'survey-react-ui';
import { Model } from 'survey-core';
import { useRequestStore } from '../stores/requestStore';
import { Loader2 } from 'lucide-react';
import { useFormLoader } from '../hooks/useFormLoader';
import 'survey-core/survey-core.min.css';

export function ProvisionWorkspaceForm() {
  const location = useLocation();
  const navigate = useNavigate();
  const addRequest = useRequestStore((state) => state.addRequest);
  const { survey, isLoading, error } = useFormLoader('/paas/provision-workspace');

  useEffect(() => {
    if (!survey) return;

    const prefillKey = `form_prefill_${location.pathname}`;
    const prefillData = localStorage.getItem(prefillKey);
    if (prefillData) {
      try {
        const data = JSON.parse(prefillData);
        const formData: Record<string, any> = {};
        
        if (data.scope) {
          const scopeMap: Record<string, string> = {
            'Just for me': 'individual',
            'For my team': 'team',
            'For multiple teams': 'multiple',
          };
          formData.scope = scopeMap[data.scope] || data.scope;
        }
        if (data.workspace_name) formData.workspace_name = data.workspace_name;
        if (data.environment) {
          const envMap: Record<string, string> = {
            'Development': 'dev',
            'Test': 'test',
            'Staging': 'stage',
            'Production': 'prod',
          };
          formData.environment = envMap[data.environment] || data.environment;
        }
        if (data.workspace_type) {
          const typeMap: Record<string, string> = {
            'Standard workspace': 'standard',
            'High-concurrency workspace': 'high_concurrency',
            'SQL warehouse workspace': 'sql_warehouse',
          };
          formData.workspace_type = typeMap[data.workspace_type] || data.workspace_type;
        }
        if (data.use_case) formData.use_case = data.use_case;
        if (data.project_name) formData.project_name = data.project_name;
        if (data.justification) formData.justification = data.justification;
        
        survey.data = formData;
        localStorage.removeItem(prefillKey);
      } catch (e) {
        console.error('Error loading prefill data:', e);
      }
    }
  }, [location.pathname, survey]);

  useEffect(() => {
    if (!survey) return;

    const handleComplete = async (survey: Model) => {
      const data = survey.data;
      const env = data.environment === 'dev' ? 'dev' : 
                  data.environment === 'test' ? 'test' :
                  data.environment === 'stage' ? 'stage' : 'prod';
      const title = `New Workspace: ${data.workspace_name} - ${data.environment || 'Dev'}`;
      
      try {
        await addRequest('workspace_provision', title, env as any, data);
        navigate('/requests');
      } catch (e) {
        console.error('Failed to submit request:', e);
        // If it fails, allow the user to see the error or stay on page
        // survey.showCompletedPage will handle the UI if we don't navigate
      }
    };

    survey.onComplete.add(handleComplete);
    survey.showCompletedPage = false; // Don't show default thank you page while navigating

    return () => {
      survey.onComplete.remove(handleComplete);
    };
  }, [survey, navigate, addRequest]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="w-8 h-8 animate-spin text-gray-400" />
      </div>
    );
  }

  if (error || !survey) {
    return (
      <div className="space-y-6">
        <div className="bg-red-50 border border-red-200 rounded-md p-6">
          <p className="text-red-800">
            {error || 'Failed to load form. Please try again later.'}
          </p>
        </div>
      </div>
    );
  }

  return <Survey model={survey} />;
}
