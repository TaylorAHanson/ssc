import { useId } from 'react';
import type { ApprovalRouting, ChangePreview } from '../../../types/metadata';
import { ROUTING_OPTIONS, plural } from './format';

/** What to do when some edits need approval, in plain language. */
export function RoutingChoice({ value, onChange, preview }: {
  value: ApprovalRouting;
  onChange: (v: ApprovalRouting) => void;
  preview: ChangePreview;
}) {
  const name = useId();
  return (
    <fieldset className="rounded-md border border-amber-200 bg-amber-50/60 p-3">
      <legend className="px-1 text-sm font-medium text-amber-900">
        {plural(preview.approval_count, 'edit')} need{preview.approval_count === 1 ? 's' : ''} approval
        {preview.approver_groups.length > 0 && ` from ${preview.approver_groups.join(', ')}`}
      </legend>
      <div className="space-y-2">
        {ROUTING_OPTIONS.filter((o) => o.value !== 'split' || preview.direct_count > 0).map((o) => (
          <label key={o.value} className="flex cursor-pointer items-start gap-2 text-sm">
            <input
              type="radio"
              name={name}
              value={o.value}
              checked={value === o.value}
              onChange={() => onChange(o.value)}
              className="mt-0.5 h-3.5 w-3.5 border-gray-300 text-primary focus:ring-primary"
            />
            <span>
              <span className="font-medium text-gray-900">{o.label}</span>
              <span className="block text-xs text-gray-600">{o.hint}</span>
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}
