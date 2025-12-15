import { useEffect } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Survey } from 'survey-react-ui';
import { Model } from 'survey-core';
import { useRequestStore } from '../stores/requestStore';
import { Loader2 } from 'lucide-react';
import { useFormLoader } from '../hooks/useFormLoader';
import 'survey-core/survey-core.min.css';

export function DataAccessForm() {
  const location = useLocation();
  const navigate = useNavigate();
  const addRequest = useRequestStore((state) => state.addRequest);
  const { survey, isLoading, error } = useFormLoader('/paas/request-access');

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
        if (data.catalog_name) formData.catalog_name = data.catalog_name;
        if (data.schema_name) formData.schema_name = data.schema_name;
        if (data.table_names) formData.table_names = data.table_names;
        if (data.justification) formData.justification = data.justification;
        if (data.project_name) formData.project_name = data.project_name;
        if (data.query_volume) {
          const volumeMap: Record<string, string> = {
            'Low (< 100 queries/day)': 'low',
            'Medium (100-1000 queries/day)': 'medium',
            'High (> 1000 queries/day)': 'high',
          };
          formData.query_volume = volumeMap[data.query_volume] || data.query_volume;
        }
        
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
      const title = `Data Access: ${data.catalog_name} - ${data.schema_name}${data.table_names ? ` (${data.table_names})` : ''}`;
      
      await addRequest('catalog_schema_table_access', title);
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

