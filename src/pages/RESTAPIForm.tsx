import { useEffect } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Survey } from 'survey-react-ui';
import { Model } from 'survey-core';
import { useRequestStore } from '../stores/requestStore';
import { Loader2 } from 'lucide-react';
import { useFormLoader } from '../hooks/useFormLoader';
import 'survey-core/survey-core.min.css';

export function RESTAPIForm() {
  const location = useLocation();
  const navigate = useNavigate();
  const addRequest = useRequestStore((state) => state.addRequest);
  const { survey, isLoading, error } = useFormLoader('/daas/rest-api');

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
        if (data.api_type) {
          const apiMap: Record<string, string> = {
            'REST API': 'rest',
            'SQL API': 'sql',
            'Delta Sharing API': 'delta_sharing',
            'Other': 'other',
          };
          formData.api_type = apiMap[data.api_type] || data.api_type;
        }
        if (data.endpoint) formData.endpoint = data.endpoint;
        if (data.rate_limit) {
          const rateMap: Record<string, string> = {
            'Low (< 100 requests/hour)': 'low',
            'Medium (100-1000 requests/hour)': 'medium',
            'High (> 1000 requests/hour)': 'high',
          };
          formData.rate_limit = rateMap[data.rate_limit] || data.rate_limit;
        }
        if (data.use_case) formData.use_case = data.use_case;
        if (data.justification) formData.justification = data.justification;
        if (data.project_name) formData.project_name = data.project_name;
        
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
      const title = `REST API Access: ${data.api_type} - ${data.endpoint}`;
      
      await addRequest('rest_api_access', title, undefined, data);
      navigate('/requests');
    };

    survey.onComplete.add(handleComplete);

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
