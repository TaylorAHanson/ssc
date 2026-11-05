import type { ContextReviewMetric } from '../services/api';

export const METRIC_LABELS: Record<ContextReviewMetric, string> = {
  completeness: 'Completeness',
  newcomer_clarity: 'Newcomer clarity',
  accuracy: 'Accuracy',
  actionability: 'Actionability',
  findability: 'Findability',
};

/** Badge colours for an AI review letter grade. */
export function gradeStyle(grade: string | null | undefined): string {
  switch (grade) {
    case 'A':
      return 'bg-green-100 text-green-800';
    case 'B':
      return 'bg-emerald-50 text-emerald-700';
    case 'C':
      return 'bg-amber-100 text-amber-800';
    case 'D':
      return 'bg-orange-100 text-orange-800';
    case 'F':
      return 'bg-red-100 text-red-800';
    default:
      return 'bg-gray-100 text-gray-600';
  }
}
