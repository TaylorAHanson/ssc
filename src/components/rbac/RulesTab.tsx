import { useCallback, useMemo, useState, type Dispatch, type SetStateAction } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../services/api';
import type { RbacChangeSetResult, RbacField, RbacPermissions, RbacRule } from '../../types/rbac';
import { Button } from '../ui/button';
import { cn } from '../../lib/utils';
import { ReviewDrawer } from './ReviewDrawer';
import { RulesTable, type CellEdit } from './RulesTable';
import { SheetPicker } from './SheetPicker';
import { StagedChangesTray } from './StagedChangesTray';
import {
  buildRows,
  duplicateCells,
  newRowCells,
  newRowKey,
  rebaseChange,
  stageCreate,
  stageDelete,
  stageEdit,
  stageUndo,
  type GridRow,
  type StagedChange,
} from './staging';
import { EmptyState, ErrorBanner, Notice, Skeleton, Spinner, type ToastMessage } from './ui';
import { useLoad } from './useLoad';
import { SETTINGS_PATH, errorMessage, loadSheets, plural } from './utils';

const RULES_PAGE = 5000;

/** The whole sheet, paging through the API when it holds more than one page. */
async function fetchAllRules(sheet: string): Promise<RbacRule[]> {
  const rules: RbacRule[] = [];
  for (;;) {
    const page = await api.getRbacRules({ sheet, limit: RULES_PAGE, offset: rules.length });
    rules.push(...page.rules);
    if (page.rules.length === 0 || rules.length >= page.total) return rules;
  }
}

interface RulesTabProps {
  permissions: RbacPermissions;
  sheetName: string | null;
  onSheetChange: (name: string) => void;
  staged: StagedChange[];
  setStaged: Dispatch<SetStateAction<StagedChange[]>>;
  /** Asks before throwing staged changes away; true when it's fine to go on. */
  confirmDiscard: () => boolean;
  notify: (toast: Omit<ToastMessage, 'id'>) => void;
  onGoToImport: () => void;
}

export function RulesTab({ permissions, sheetName, onSheetChange, staged, setStaged, confirmDiscard, notify, onGoToImport }: RulesTabProps) {
  const sheetsState = useLoad(loadSheets);
  const sheets = useMemo(() => sheetsState.data ?? [], [sheetsState.data]);
  const sheet =
    sheets.find((s) => s.name === sheetName) ?? sheets.find((s) => s.row_count > 0) ?? sheets[0] ?? null;
  const selectedName = sheet?.name ?? null;

  const loadRules = useMemo(() => (selectedName ? () => fetchAllRules(selectedName) : null), [selectedName]);
  const rulesState = useLoad(loadRules);
  const rules = rulesState.data;
  const rows = useMemo(() => buildRows(rules ?? [], staged), [rules, staged]);
  const [reviewing, setReviewing] = useState(false);

  const rowNumbers = useMemo(() => new Map(rows.map((r) => [r.key, r.number])), [rows]);
  const labelFor = useCallback(
    (index: number) => {
      const s = staged[index];
      if (!s) return '';
      const n = rowNumbers.get(s.key);
      if (s.op === 'create') return `New row ${n ?? ''}`.trim();
      return `Row ${n ?? '?'} (${s.op === 'delete' ? 'delete' : 'edit'})`;
    },
    [staged, rowNumbers],
  );

  const selectSheet = (name: string) => {
    if (name === selectedName || !confirmDiscard()) return;
    setStaged([]);
    onSheetChange(name);
  };

  const onEdit = (row: GridRow, field: RbacField, value: string) => setStaged((prev) => stageEdit(prev, row, field, value));
  const onReplace = (edits: CellEdit[]) =>
    setStaged((prev) => edits.reduce((acc, e) => stageEdit(acc, e.row, e.field, e.value), prev));
  const onDelete = (row: GridRow) => setStaged((prev) => stageDelete(prev, row));
  const onUndo = (row: GridRow) => setStaged((prev) => stageUndo(prev, row.key));
  const onAdd = (from?: GridRow) => {
    const key = newRowKey();
    const cells = sheet ? (from ? duplicateCells(sheet, from) : newRowCells(sheet, rules ?? [])) : {};
    if (sheet) setStaged((prev) => stageCreate(prev, key, sheet.name, cells));
    return { key, cells };
  };

  const discard = () => {
    if (window.confirm(`Discard ${plural(staged.length, 'staged change')}? Nothing has been saved.`)) setStaged([]);
  };

  const afterConflict = (next: StagedChange[]) => {
    setStaged(next);
    if (next.length === 0) setReviewing(false);
    rulesState.reload();
  };

  const onCommitted = (result: RbacChangeSetResult) => {
    setReviewing(false);
    setStaged([]);
    rulesState.reload();
    sheetsState.reload();
    if (result.request_id) {
      notify({
        text: 'Request filed. Another approver must approve it before the rows change.',
        link: { to: `/requests/${result.request_id}`, label: 'View request' },
      });
    } else {
      notify({ text: `Saved${result.change_set_id ? ` as change set ${result.change_set_id.slice(0, 8)}` : ''}. It’s in History and goes out with the next export.` });
    }
  };

  if (sheetsState.error) {
    return <ErrorBanner message={errorMessage(sheetsState.error, 'Could not load the sheets')} onRetry={sheetsState.reload} />;
  }
  if (!sheetsState.data) return <Skeleton rows={6} />;
  if (sheets.length === 0) {
    return (
      <EmptyState title="No sheets are configured">
        The managed sheets come from the environments set in{' '}
        <Link to={SETTINGS_PATH} className="text-accent hover:underline">Admin → Settings → Access Rules</Link>.
      </EmptyState>
    );
  }
  if (sheets.every((s) => s.row_count === 0) && rules && rules.length === 0 && staged.length === 0) {
    return (
      <EmptyState title="No rules yet">
        <p>The rules arrive with the one-time import of the RBAC workbook.</p>
        {permissions.can_import ? (
          <Button size="sm" className="mt-3" onClick={onGoToImport}>Go to Import</Button>
        ) : (
          <p className="mt-1">A Platform Admin runs it from the Import tab.</p>
        )}
      </EmptyState>
    );
  }

  const plane = permissions.planes.find((p) => p.plane === sheet?.plane);

  return (
    <div className={cn('space-y-3', staged.length > 0 && 'pb-24')}>
      <SheetPicker sheets={sheets} selected={selectedName} onSelect={selectSheet} />
      {sheet && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-gray-500">
          <span className="font-mono text-[13px] font-medium text-gray-900">{sheet.name}</span>
          <span>
            {sheet.export_path ? (
              <>Exported to <code className="text-gray-700">{sheet.export_path}</code></>
            ) : (
              <span className="text-amber-700">Not exported: no path is set for it</span>
            )}
          </span>
          {rulesState.loading && rules && <Spinner label="Refreshing…" className="text-xs" />}
        </div>
      )}
      {sheet && !sheet.editable && (
        <Notice kind="info">
          View only. Rows on this sheet are edited by{' '}
          {plane?.approver_group ? <code>{plane.approver_group}</code> : 'Platform Admins (no approver group is set)'}
          {plane?.approver_group ? ' and by Platform Admins' : ''}.
        </Notice>
      )}
      {rulesState.error ? (
        <ErrorBanner message={errorMessage(rulesState.error, 'Could not load the rules')} onRetry={rulesState.reload} />
      ) : !rules || !sheet ? (
        <Skeleton rows={8} />
      ) : (
        <RulesTable
          key={sheet.name}
          sheet={sheet}
          rows={rows}
          editable={sheet.editable}
          onEdit={onEdit}
          onDelete={onDelete}
          onUndo={onUndo}
          onAdd={onAdd}
          onReplace={onReplace}
          maxChanges={permissions.max_changes_per_commit}
        />
      )}
      <StagedChangesTray staged={staged} onDiscard={discard} onReview={() => setReviewing(true)} />
      {reviewing && sheet && staged.length > 0 && (
        <ReviewDrawer
          staged={staged}
          sheet={sheet}
          labelFor={labelFor}
          minReasonLength={permissions.min_reason_length}
          onClose={() => setReviewing(false)}
          onCommitted={onCommitted}
          onDrop={(ruleId) => {
            afterConflict(stageUndo(staged, ruleId));
          }}
          onReapply={(current) => {
            afterConflict(rebaseChange(staged, current));
          }}
        />
      )}
    </div>
  );
}
