import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Tags } from 'lucide-react';
import { getConfig, getWhoAmI, listKeys } from '../../../services/metadataApi';
import { useUserStore } from '../../../stores/userStore';
import type { ChangeSet, ObjectDetail } from '../../../types/metadata';
import { ErrorBanner, Notice, Skeleton, Toast, type ToastMessage } from '../../../components/rbac/ui';
import { ChangeSetDrawer } from '../../../components/admin/metadata/ChangeSetDrawer';
import { ChangesTab } from '../../../components/admin/metadata/ChangesTab';
import { MetadataContext, type MetadataContextValue } from '../../../components/admin/metadata/context';
import { DriftTab } from '../../../components/admin/metadata/DriftTab';
import { errorMessage } from '../../../components/admin/metadata/format';
import { CircuitBreakerBanner } from '../../../components/admin/metadata/ModeBanner';
import { ObjectDrawer } from '../../../components/admin/metadata/ObjectDrawer';
import { ObjectsTab } from '../../../components/admin/metadata/ObjectsTab';
import { PrincipalsTab } from '../../../components/admin/metadata/PrincipalsTab';
import { PromoteDialog } from '../../../components/admin/metadata/PromoteDialog';
import { RevertDialog } from '../../../components/admin/metadata/RevertDialog';
import { RunDrawer } from '../../../components/admin/metadata/RunDrawer';
import { RunsTab } from '../../../components/admin/metadata/RunsTab';
import { StagedTray } from '../../../components/admin/metadata/StagedTray';
import { hasEdits, useStagingStore } from '../../../components/admin/metadata/staging';
import { useAsync } from '../../../components/admin/metadata/useAsync';

type Tab = 'objects' | 'changes' | 'drift' | 'runs' | 'principals';

const TAB_LABELS: Record<Tab, string> = {
  objects: 'Objects',
  changes: 'Changes',
  drift: 'Drift',
  runs: 'Reconciler runs',
  principals: 'System principals',
};

type Panel =
  | { kind: 'object'; name: string; column: string | null }
  | { kind: 'changeset'; id: string }
  | { kind: 'run'; id: string }
  | { kind: 'promote'; detail: ObjectDetail }
  | { kind: 'revert'; changeSet: ChangeSet };

/** Search, edit and audit table and column metadata declared here and applied to Unity Catalog. */
export function MetadataManager() {
  const [params, setParams] = useSearchParams();
  const [refreshToken, setRefreshToken] = useState(0);
  const roleOverride = useUserStore((s) => s.activeRoleOverride);
  const [panel, setPanel] = useState<Panel | null>(null);
  const [toast, setToast] = useState<ToastMessage | null>(null);

  const load = useCallback(() => Promise.all([getWhoAmI(), getConfig(), listKeys({ limit: 500 })]), []);
  const { data, error, reload } = useAsync(load, { keepPrevious: true, token: `${refreshToken}:${roleOverride ?? ''}` });
  const stagedCount = useStagingStore((s) => s.order.filter((k) => s.targets[k] && hasEdits(s.targets[k])).length);

  // Staged edits live only in this tab's memory; warn before a reload or close loses them.
  useEffect(() => {
    if (!stagedCount) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, [stagedCount]);

  const bumpRefresh = useCallback(() => setRefreshToken((t) => t + 1), []);
  const dismissToast = useCallback(() => setToast(null), []);
  const ctx = useMemo<MetadataContextValue | null>(() => {
    if (!data) return null;
    const [whoami, config, keys] = data;
    return {
      whoami,
      config,
      keys,
      isAdmin: whoami.actor.is_admin,
      refreshToken,
      bumpRefresh,
      openObject: (name, column) => setPanel({ kind: 'object', name, column: column ?? null }),
      openChangeSet: (id) => setPanel({ kind: 'changeset', id }),
      openRun: (id) => setPanel({ kind: 'run', id }),
      notify: (text, opts) => setToast({ id: Date.now(), text, ...opts }),
    };
  }, [data, refreshToken, bumpRefresh]);

  const tabs: Tab[] = ctx?.isAdmin ? ['objects', 'changes', 'drift', 'runs', 'principals'] : ['objects', 'changes', 'drift', 'runs'];
  const requested = params.get('tab') as Tab | null;
  const active: Tab = requested && tabs.includes(requested) ? requested : 'objects';
  const switchTab = (t: Tab) => setParams(t === 'objects' ? {} : { tab: t }, { replace: true });
  const closePanel = () => setPanel(null);

  let body: ReactNode;
  if (error && !ctx) {
    body = <ErrorBanner message={errorMessage(error, 'Couldn’t load the Metadata Manager')} onRetry={reload} />;
  } else if (!ctx) {
    body = <Skeleton rows={8} />;
  } else if (!ctx.whoami.can_browse) {
    body = (
      <Notice kind="warn" title="You can’t browse metadata here">
        {ctx.whoami.message ?? 'Ask a governance admin for access.'}
      </Notice>
    );
  } else {
    body = (
      <>
        {ctx.isAdmin && active !== 'runs' && (
          <CircuitBreakerBanner
            action={
              <button type="button" onClick={() => switchTab('runs')} className="font-medium underline">
                Review on the Reconciler runs tab
              </button>
            }
          />
        )}
        <div role="tablist" aria-label="Metadata Manager sections" className="flex flex-wrap gap-2 border-b border-gray-200">
          {tabs.map((t) => (
            <button
              key={t}
              type="button"
              role="tab"
              id={`metadata-tab-${t}`}
              aria-selected={active === t}
              aria-controls="metadata-tabpanel"
              onClick={() => switchTab(t)}
              className={`flex items-center gap-2 px-4 py-2 text-sm font-medium transition-colors ${active === t ? 'border-b-2 border-primary text-primary' : 'text-gray-600 hover:text-gray-900'}`}
            >
              {TAB_LABELS[t]}
              {t === 'objects' && stagedCount > 0 && (
                <span className="rounded-full bg-amber-100 px-2 text-xs text-amber-800">{stagedCount} staged</span>
              )}
            </button>
          ))}
        </div>
        <div role="tabpanel" id="metadata-tabpanel" aria-labelledby={`metadata-tab-${active}`} className="space-y-4">
          {active === 'objects' && <ObjectsTab />}
          {active === 'changes' && <ChangesTab />}
          {active === 'drift' && <DriftTab onShowRuns={() => switchTab('runs')} />}
          {active === 'runs' && <RunsTab />}
          {active === 'principals' && <PrincipalsTab />}
          {(active === 'objects' || active === 'drift') && <StagedTray />}
        </div>
      </>
    );
  }

  return (
    <div className="space-y-5">
      <div>
        <h2 className="flex items-center gap-2 text-xl font-bold text-gray-900">
          <Tags className="h-5 w-5" /> Metadata Manager
        </h2>
        <p className="mt-1 text-sm text-gray-500">
          Find tables and columns, see who set each tag and description, and change them with approvals where needed. Changes saved here are applied to Unity
          Catalog in the background.
        </p>
      </div>
      {ctx ? <MetadataContext.Provider value={ctx}>{body}{renderPanel(panel, closePanel, setPanel)}</MetadataContext.Provider> : body}
      {toast && <Toast toast={toast} onDismiss={dismissToast} />}
    </div>
  );
}

function renderPanel(panel: Panel | null, close: () => void, open: (p: Panel) => void): ReactNode {
  if (!panel) return null;
  switch (panel.kind) {
    case 'object':
      return (
        <ObjectDrawer
          key={`${panel.name}::${panel.column ?? ''}`}
          name={panel.name}
          column={panel.column}
          onClose={close}
          onPromote={(detail) => open({ kind: 'promote', detail })}
        />
      );
    case 'changeset':
      return <ChangeSetDrawer key={panel.id} id={panel.id} onClose={close} onRevert={(changeSet) => open({ kind: 'revert', changeSet })} />;
    case 'run':
      return <RunDrawer key={panel.id} id={panel.id} onClose={close} />;
    case 'promote':
      return <PromoteDialog detail={panel.detail} onClose={close} />;
    case 'revert':
      return <RevertDialog changeSet={panel.changeSet} onClose={close} />;
  }
}
