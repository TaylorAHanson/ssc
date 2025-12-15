import { AlertCircle, BookOpen, CheckCircle2 } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from './ui/card';
import { Button } from './ui/button';
import { useRequestStore } from '../stores/requestStore';
import { mockApi } from '../services/mockApi';

interface TrainingBlockerProps {
  requestId: string;
  requiresTraining: boolean;
  trainingCompleted: boolean;
}

export function TrainingBlocker({
  requestId,
  requiresTraining,
  trainingCompleted,
}: TrainingBlockerProps) {
  const updateRequest = useRequestStore((state) => state.updateRequest);

  if (!requiresTraining) {
    return null;
  }

  const handleCompleteTraining = async () => {
    const updated = await mockApi.completeTraining(requestId);
    await updateRequest(requestId, {
      trainingCompleted: true,
      stateMachine: updated.stateMachine,
    });
  };

  if (trainingCompleted) {
    return (
      <Card className="border-green-200 bg-green-50">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-green-800">
            <CheckCircle2 className="w-5 h-5" />
            Training Completed
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-green-700">
            You have completed the required training. Your request can now proceed to provisioning.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="border-yellow-200 bg-yellow-50">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-yellow-800">
          <AlertCircle className="w-5 h-5" />
          Training Pending
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-start gap-3">
          <BookOpen className="w-5 h-5 text-yellow-700 mt-0.5" />
          <div className="flex-1">
            <p className="text-sm font-medium text-yellow-900 mb-1">
              Interactive Workshop Required
            </p>
            <p className="text-sm text-yellow-700">
              Before your workspace can be provisioned, you must complete the required
              training workshop. This ensures compliance and proper usage of Databricks resources.
            </p>
          </div>
        </div>
        <div className="pt-2 border-t border-yellow-200">
          <Button
            onClick={handleCompleteTraining}
            className="bg-yellow-600 hover:bg-yellow-700 text-white"
          >
            Mark Training as Complete
          </Button>
          <p className="text-xs text-yellow-600 mt-2">
            Note: In production, this would redirect to the training portal.
          </p>
        </div>
      </CardContent>
    </Card>
  );
}

