import { useState, useEffect } from 'react';
import { Model } from 'survey-core';
import { getForm } from '../services/api';

/**
 * Hook to load a form schema from the API and create a SurveyJS model.
 */
export function useFormLoader(formPath: string) {
  const [survey, setSurvey] = useState<Model | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let isMounted = true;

    const loadForm = async () => {
      setIsLoading(true);
      setError(null);

      try {
        const formData = await getForm(formPath);
        if (isMounted) {
          const model = new Model(formData.schema);
          setSurvey(model);
        }
      } catch (err) {
        if (isMounted) {
          setError(err instanceof Error ? err.message : 'Failed to load form');
          console.error('Error loading form:', err);
        }
      } finally {
        if (isMounted) {
          setIsLoading(false);
        }
      }
    };

    loadForm();

    return () => {
      isMounted = false;
    };
  }, [formPath]);

  return { survey, isLoading, error };
}

