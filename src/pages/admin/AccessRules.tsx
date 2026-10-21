import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { RbacApiError, api } from '../../services/api';
import { AccessRulesHeader, ApproverStrip, NoAccessPanel } from '../../components/rbac/AccessRulesHeader';
import { ExportsTab } from '../../components/rbac/ExportsTab';
import { HistoryTab } from '../../components/rbac/HistoryTab';
import { ImportTab } from '../../components/rbac/ImportTab';
import { RulesTab } from '../../components/rbac/RulesTab';
import type { StagedChange } from '../../components/rbac/staging';
import { ErrorBanner, Skeleton, Toast, type ToastMessage } from '../../components/rbac/ui';
import { useLoad } from '../../components/rbac/useLoad';
import { errorMessage, plural } from '../../components/rbac/utils';

type Tab = 'rules' | 'history' | 'exports' | 'import';

const TAB_LABELS: Record<Tab, string> = {
  rules: 'Rules',
  history: 'History',
  exports: 'Exports',
  import: 'Import',
};

const loadPermissions = () => api.getRbacPermissions();

// Staged edits outlive the page while the user moves around the app (the router can't
// block in-app links); reloading or closing the tab is guarded by beforeunload instead.
let kept: { staged: StagedChange[]; sheetName: string | null } = { staged: [], sheetName: null };

export function AccessRules() {
  const permissions = useLoad(loadPermissions);
  const [tab, setTab] = useState<Tab>('rules');
  const [sheetName, setSheetName] = useState<string | null>(() => kept.sheetName);
  const [staged, setStaged] = useState<StagedChange[]>(() => kept.staged);
  const [toast, setToast] = useState<ToastMessage | null>(null);

  useEffect(() => {
    kept = { staged, sheetName };
  }, [staged, sheetName]);

  const notify = useCallback((t: Omit<ToastMessage, 'id'>) => setToast({ ...t, id: Date.now() }), []);
  const dismissToast = useCallback(() => setToast(null), []);

  const dirty = staged.length > 0;
  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, [dirty]);

  const confirmDiscard = useCallback(
    () =>
      staged.length === 0 ||
      window.confirm(
        staged.length === 1
          ? 'You have 1 staged change that hasn’t been saved. Discard it?'
          : `You have ${plural(staged.length, 'staged change')} that haven’t been saved. Discard them?`,
      ),
    [staged.length],
  );

  const switchTab = (next: Tab) => {
    if (next === tab || !confirmDiscard()) return;
    setStaged([]);
    setTab(next);
  };

  const data = permissions.data;
  const status = permissions.error instanceof RbacApiError ? permissions.error.status : null;

  let body: ReactNode;
  if (permissions.error && status !== 403) {
    body = (
      <ErrorBanner
        message={errorMessage(permissions.error, 'Could not check your access to Access Rules')}
        onRetry={status === 404 ? undefined : permissions.reload}
      />
    );
  } else if (status === 403 || (data && !data.can_view)) {
    body = <NoAccessPanel />;
  } else if (!data) {
    body = (
      <div className="space-y-4">
        <Skeleton rows={1} />
        <Skeleton rows={6} />
      </div>
    );
  } else {
    const tabs: Tab[] = data.can_import ? ['rules', 'history', 'exports', 'import'] : ['rules', 'history', 'exports'];
    const active: Tab = tabs.includes(tab) ? tab : 'rules';
    body = (
      <>
        <ApproverStrip planes={data.planes} />
        <div role="tablist" aria-label="Access Rules sections" className="flex gap-1 border-b border-gray-200">
          {tabs.map((t) => (
            <button
              key={t}
              type="button"
              role="tab"
              id={`rbac-tab-${t}`}
              aria-selected={active === t}
              aria-controls="rbac-tabpanel"
              onClick={() => switchTab(t)}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-[13px] font-medium transition-colors ${active === t ? 'border-b-2 border-primary text-primary' : 'text-gray-600 hover:text-gray-900'}`}
            >
              {TAB_LABELS[t]}
              {t === 'rules' && dirty && (
                <span className="rounded-full bg-amber-100 px-1.5 text-[11px] text-amber-800">{staged.length} staged</span>
              )}
            </button>
          ))}
        </div>
        <div role="tabpanel" id="rbac-tabpanel" aria-labelledby={`rbac-tab-${active}`}>
          {active === 'rules' && (
            <RulesTab
              permissions={data}
              sheetName={sheetName}
              onSheetChange={setSheetName}
              staged={staged}
              setStaged={setStaged}
              confirmDiscard={confirmDiscard}
              notify={notify}
              onGoToImport={() => switchTab('import')}
            />
          )}
          {active === 'history' && <HistoryTab permissions={data} notify={notify} />}
          {active === 'exports' && <ExportsTab permissions={data} notify={notify} />}
          {active === 'import' && <ImportTab permissions={data} notify={notify} onOpenHistory={() => switchTab('history')} />}
        </div>
      </>
    );
  }

  return (
    <div className="space-y-4">
      <AccessRulesHeader />
      {body}
      {toast && <Toast toast={toast} onDismiss={dismissToast} />}
    </div>
  );
}
