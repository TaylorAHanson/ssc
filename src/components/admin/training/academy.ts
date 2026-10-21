import type { AcademyCourse } from '../../../services/api';

/** "Self-paced · 2 hrs · Free" */
export function academyMeta(c: AcademyCourse): string {
  return [c.type_label, c.duration, c.is_free ? 'Free' : 'Paid'].filter(Boolean).join(' · ');
}
