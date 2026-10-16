import { useEffect, useMemo, useState } from 'react';
import { Loader2, Users } from 'lucide-react';
import { api } from '../../services/api';
import type { RbacChangeSetPreview, RbacChangeSetResult, RbacFinding, RbacRowDiff, RbacRule, RbacSheetInfo } from '../../types/rbac';
import { Button } from '../ui/button';
import { Textarea } from '../ui/textarea';
import { DiffView } from './DiffView';
import { FindingList } from './FindingList';
import { toRowChange, type StagedChange } from './staging';
import { Drawer, ErrorBanner, Notice, SectionTitle, Spinner } from './ui';
import { blockedPreviewOf, cellValue, conflictOf, errorMessage, plural, ruleCells, type RbacConflictDetail } from './utils';

interface ReviewDrawerProps {
  staged: StagedChange[];
  sheet: RbacSheetInfo;
  /** Where a staged change sits in the grid, e.g. "Row 12 (edit)". */
  labelFor: (index: number) => string;
  onClose: () => void;
  onCommitted: (result: RbacChangeSetResult) => void;
  onDrop: (ruleId: string) => void;
  onReapply: (current: RbacRule) => void;
  minReasonLength: number;
}

interface Checked {
  key: string;
  preview?: RbacChangeSetPreview;
  error?: unknown;
}

/** Dry-runs the staged changes, shows diffs and findings, and commits them with a reason. */
export function ReviewDrawer({ staged, sheet, labelFor, onClose, onCommitted, onDrop, onReapply, minReasonLength }: ReviewDrawerProps) {
  const changes = useMemo(() => staged.map(toRowChange), [staged]);
  const [tick, setTick] = useState(0);
  const key = `${JSON.stringify(changes)}#${tick}`;
  const [checked, setChecked] = useState<Checked | null>(null);
  const [reason, setReason] = useState('');
  const [committing, setCommitting] = useState(false);
  const [commitFailure, setCommitFailure] = useState<{ key: string; error: unknown } | null>(null);

  useEffect(() => {
    let active = true;
    api.validateRbacChanges({ changes, reason: '' }).then(
      (preview) => {
        if (active) setChecked({ key, preview });
      },
      (error: unknown) => {
        if (active) setChecked({ key, error });
      },
    );
    return () => {
      active = false;
    };
  }, [changes, key]);

  const current = checked?.key === key ? checked : null;
  const failure = commitFailure?.key === key ? commitFailure.error : null;
  const validating = !current;
  // A commit rejected by policy (422) carries a fresh preview; it wins over the dry run.
  const preview = blockedPreviewOf(failure) ?? current?.preview ?? null;
  const conflict = conflictOf(failure) ?? conflictOf(current?.error);
  const validateError = current?.error && !conflict ? errorMessage(current.error, 'Could not check the changes') : null;
  const commitError = failure && !conflict && !blockedPreviewOf(failure) ? errorMessage(failure, 'Commit failed') : null;
  const blocked = !!preview?.blocked;
  const twoPerson = !!preview?.two_person_required;
  const reasonShort = reason.trim().length < Math.max(minReasonLength, 1);
  const canCommit = !!preview && !blocked && !conflict && !validating && !committing && !reasonShort;
  const conflictIndex = conflict ? staged.findIndex((s) => s.key === conflict.rule_id) : -1;

  const findingContext = (f: RbacFinding) =>
    f.change_index !== null && f.change_index !== undefined && staged[f.change_index] ? labelFor(f.change_index) : f.sheet ?? '';

  const diffLabels = useMemo(() => {
    const byRule = new Map<string, string>();
    const creates: string[] = [];
    staged.forEach((s, i) => {
      if (s.op === 'create') creates.push(labelFor(i));
      else byRule.set(s.rule.id, labelFor(i));
    });
    return { byRule, creates };
  }, [staged, labelFor]);

  const diffContext = (diff: RbacRowDiff, index: number) => {
    if (diff.rule_id && diffLabels.byRule.has(diff.rule_id)) return diffLabels.byRule.get(diff.rule_id);
    if (diff.op === 'create' && preview) {
      const nth = preview.diffs.slice(0, index).filter((d) => d.op === 'create').length;
      return diffLabels.creates[nth];
    }
    return undefined;
  };

  const commit = async () => {
    setCommitting(true);
    try {
      const result = await api.commitRbacChanges({ changes, reason: reason.trim() });
      onCommitted(result);
    } catch (error) {
      setCommitFailure({ key, error });
    } finally {
      setCommitting(false);
    }
  };

  return (
    <Drawer
      title={`Review ${plural(staged.length, 'change')}`}
      subtitle={<>On <span className="font-mono">{sheet.name}</span>. Saved all together or not at all.</>}
      onClose={onClose}
      footer={
        <div className="space-y-3">
          <div>
            <label htmlFor="rbac-reason" className="mb-1 block text-sm font-medium text-gray-700">
              Reason <span className="text-red-600">*</span>
            </label>
            <Textarea
              id="rbac-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
              placeholder="Why are you making this change? It’s kept in History with the diff."
            />
            {minReasonLength > 1 && reasonShort && (
              <p className="mt-1 text-xs text-gray-500">At least {minReasonLength} characters.</p>
            )}
          </div>
          {commitError && <ErrorBanner message={commitError} />}
          <div className="flex items-center justify-end gap-2">
            <Button variant="outline" onClick={onClose}>Keep editing</Button>
            <Button onClick={commit} disabled={!canCommit} title={blocked ? 'Fix the blocking findings first' : undefined}>
              {committing && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {twoPerson ? 'File request' : `Commit ${plural(staged.length, 'change')}`}
            </Button>
          </div>
        </div>
      }
    >
      {validating && <Spinner label="Checking the changes against policy…" />}
      {validateError && <ErrorBanner message={validateError} onRetry={() => setTick((t) => t + 1)} />}
      {conflict && (
        <ConflictPanel
          conflict={conflict}
          sheet={sheet}
          mine={conflictIndex >= 0 ? staged[conflictIndex] : null}
          label={conflictIndex >= 0 ? labelFor(conflictIndex) : null}
          onDrop={() => onDrop(conflict.rule_id)}
          onReapply={conflict.current && conflict.current.status !== 'deleted' ? () => onReapply(conflict.current as RbacRule) : undefined}
        />
      )}
      {preview && !conflict && (
        <>
          {twoPerson && (
            <Notice kind="info" title="Another approver must approve this">
              <span className="inline-flex items-start gap-1.5">
                <Users className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                This environment has the two-person rule on. Saving files a request that another member of the
                approver group must approve; the rows change once it’s approved.
              </span>
            </Notice>
          )}
          <FindingList
            findings={preview.findings}
            context={findingContext}
            sheet={sheet}
            denyHint={
              preview.findings.every((f) => f.severity !== 'deny' || f.field === 'reason')
                ? 'Write a longer reason to commit.'
                : 'Fix or undo these rows to commit.'
            }
          />
          <div>
            <SectionTitle>Changes</SectionTitle>
            <DiffView diffs={preview.diffs} sheets={[sheet]} context={diffContext} />
          </div>
        </>
      )}
    </Drawer>
  );
}

function ConflictPanel({ conflict, sheet, mine, label, onDrop, onReapply }: {
  conflict: RbacConflictDetail;
  sheet: RbacSheetInfo;
  mine: StagedChange | null;
  label: string | null;
  onDrop: () => void;
  onReapply?: () => void;
}) {
  const current = conflict.current;
  const loaded = mine && mine.op !== 'create' ? ruleCells(mine.rule) : null;
  const now = current ? ruleCells(current) : null;
  const gone = !current || current.status === 'deleted';
  return (
    <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm">
      <p className="font-medium text-red-800">Someone changed this row{label ? ` (${label})` : ''}</p>
      <p className="mt-1 text-red-700">
        {gone
          ? 'It has been deleted since you loaded the sheet, so your change can’t be applied.'
          : 'It was saved by someone else after you loaded the sheet. Here is what it holds now; changed values are highlighted.'}
      </p>
      {now && !gone && (
        <table className="mt-2 w-full overflow-hidden rounded border border-red-100 bg-white text-xs">
          <tbody>
            {sheet.columns.map((col, i) => {
              const value = cellValue(now, col, i);
              const changed = loaded ? cellValue(loaded, col, i) !== value : false;
              return (
                <tr key={i} className={changed ? 'bg-amber-50' : undefined}>
                  <th scope="row" className="w-40 px-2 py-1 text-left font-medium text-gray-500">{col.header}</th>
                  <td className="px-2 py-1 text-gray-800">
                    {value || <span className="text-gray-300">—</span>}
                    {changed && loaded && (
                      <span className="ml-2 text-gray-400">(you loaded “{cellValue(loaded, col, i)}”)</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        <Button size="sm" variant="outline" onClick={onDrop}>Drop my change</Button>
        {onReapply && (
          <Button size="sm" onClick={onReapply} title="Keep your edits on top of the row as it is now">
            Re-apply my change on the new version
          </Button>
        )}
      </div>
    </div>
  );
}
