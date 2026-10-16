import { Button } from '../ui/button';
import { countByOp, type StagedChange } from './staging';
import { plural } from './utils';

/** Sticks to the bottom of the page while changes are staged. */
export function StagedChangesTray({ staged, onDiscard, onReview }: {
  staged: StagedChange[];
  onDiscard: () => void;
  onReview: () => void;
}) {
  if (staged.length === 0) return null;
  const c = countByOp(staged);
  const parts = [
    c.update && `${c.update} edited`,
    c.create && `${c.create} added`,
    c.delete && `${c.delete} deleted`,
  ].filter(Boolean);
  return (
    <div className="sticky bottom-0 z-20 -mx-1 pt-2" role="region" aria-label="Staged changes">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-primary/30 bg-white px-4 py-3 shadow-lg">
        <div className="text-sm">
          <span className="font-semibold text-gray-900">{plural(staged.length, 'change')} staged</span>
          <span className="text-gray-500"> · {parts.join(', ')} · nothing is saved until you review and commit</span>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={onDiscard}>Discard</Button>
          <Button size="sm" onClick={onReview}>Review</Button>
        </div>
      </div>
    </div>
  );
}
