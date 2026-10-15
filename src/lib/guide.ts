/**
 * Deep links into the in-app Guide (`src/pages/Guide.tsx`).
 * `backend/tests/unit/test_guide_links.py` fails if one stops resolving.
 */
import { useBrandingStore } from '../stores/brandingStore';

export const GUIDE_HOME = '/guide';
export const GUIDE_REQUESTS = '/guide/requests-and-approvals#track-your-requests';
export const GUIDE_APPROVALS = '/guide/requests-and-approvals#approve-or-reject-a-request';
export const GUIDE_WORKFLOW_STUDIO = '/guide/admin/workflows-and-tools#workflow-studio';

/** Admins can hide the Guide under Admin → Settings → Features & Navigation. */
export function useGuideEnabled(): boolean {
  return useBrandingStore((s) => s.uiTabs?.guide !== false);
}
