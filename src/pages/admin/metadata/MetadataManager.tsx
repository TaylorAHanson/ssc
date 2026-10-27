import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { Link, Navigate, NavLink, Route, Routes, useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { Settings2, Tags, X } from 'lucide-react';
import { getConfig, getWhoAmI, listKeys } from '../../../services/metadataApi';
import { useUserStore } from '../../../stores/userStore';
import type { ChangeSet } from '../../../types/metadata';
import { ErrorBanner, Notice, Skeleton, Toast, type ToastMessage } from '../../../components/rbac/ui';
import { cn } from '../../../lib/utils';
import { AdminPage } from '../../../components/admin/metadata/AdminPage';
import { BrowsePage } from '../../../components/admin/metadata/BrowsePage';
import { ChangeSetDrawer } from '../../../components/admin/metadata/ChangeSetDrawer';
import { DatasetPage } from '../../../components/admin/metadata/DatasetPage';
import { DatasetsPage } from '../../../components/admin/metadata/DatasetsPage';
import { MetadataContext, type MetadataContextValue } from '../../../components/admin/metadata/context';
import { errorMessage } from '../../../components/admin/metadata/format';
import { HistoryPage } from '../../../components/admin/metadata/HistoryPage';
import { CircuitBreakerBanner } from '../../../components/admin/metadata/ModeBanner';
import { ObjectPage } from '../../../components/admin/metadata/ObjectPage';
import { METADATA_BASE, adminPath, datasetsPath, historyPath, objectPath } from '../../../components/admin/metadata/paths';
import { RevertDialog } from '../../../components/admin/metadata/RevertDialog';
import { RunDrawer } from '../../../components/admin/metadata/RunDrawer';
import { UnsavedBar } from '../../../components/admin/metadata/UnsavedBar';
import { hasEdits, useStagingStore } from '../../../components/admin/metadata/staging';
import { useAsync } from '../../../components/admin/metadata/useAsync';

type Panel = { kind: 'changeset'; id: string } | { kind: 'run'; id: string } | { kind: 'revert'; changeSet: ChangeSet };

const WELCOME_KEY = 'metadata-manager.welcome-dismissed';

/** Find a table, change its tags and description, and see what changed; admins also get sync health. */
export function MetadataManager() {
  const [refreshToken, setRefreshToken] = useState(0);
  const roleOverride = useUserStore((s) => s.activeRoleOverride);
  const navigate = useNavigate();
  const location = useLocation();
  // A drawer belongs to the page it was opened on; following a link inside it closes it.
  const [opened, setOpened] = useState<{ panel: Panel; at: string } | null>(null);
  const panel = opened?.at === location.pathname ? opened.panel : null;
  const setPanel = useCallback((p: Panel | null) => setOpened(p ? { panel: p, at: location.pathname } : null), [location.pathname]);
  const [toast, setToast] = useState<ToastMessage | null>(null);

  const load = useCallback(() => Promise.all([getWhoAmI(), getConfig(), listKeys({ limit: 500 })]), []);
  const { data, error, reload } = useAsync(load, { keepPrevious: true, token: `${refreshToken}:${roleOverride ?? ''}` });
  const unsavedCount = useStagingStore((s) => s.order.filter((k) => s.targets[k] && hasEdits(s.targets[k])).length);

  // Unsaved changes live only in this tab's memory; warn before a reload or close loses them.
  useEffect(() => {
    if (!unsavedCount) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, [unsavedCount]);

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
      ownedOnly: whoami.browse_scope === 'owned',
      refreshToken,
      bumpRefresh,
      openObject: (name, column) => navigate(objectPath(name, column)),
      openChangeSet: (id) => setPanel({ kind: 'changeset', id }),
      openRun: (id) => setPanel({ kind: 'run', id }),
      notify: (text, opts) => setToast({ id: Date.now(), text, ...opts }),
    };
  }, [data, refreshToken, bumpRefresh, navigate, setPanel]);

  const onAdmin = location.pathname.startsWith(adminPath);
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
        {ctx.isAdmin && !onAdmin && (
          <CircuitBreakerBanner
            action={
              <Link to={adminPath} className="font-medium underline">
                Review it on the Sync &amp; admin page
              </Link>
            }
          />
        )}
        {ctx.ownedOnly && (
          <Notice kind="info" title={ctx.whoami.message ?? 'Showing only the objects you own.'}>
            Catalogs, search, datasets and history list only objects whose owner tag names one of your groups, or that you own in Unity Catalog
            when that counts. Governance sees everything.
          </Notice>
        )}
        <Routes>
          <Route index element={<LegacyTabRedirect />} />
          <Route path="browse/:catalog" element={<BrowsePage key={location.pathname} />} />
          <Route path="browse/:catalog/:schema" element={<BrowsePage key={location.pathname} />} />
          <Route path="browse/:catalog/:schema/:table" element={<ObjectPage />} />
          <Route path="datasets" element={<DatasetsPage />} />
          <Route path="datasets/:dataset" element={<DatasetPage key={location.pathname} />} />
          <Route path="history" element={<HistoryPage />} />
          <Route path="admin" element={ctx.isAdmin ? <AdminPage /> : <Navigate to={METADATA_BASE} replace />} />
          <Route path="*" element={<Navigate to={METADATA_BASE} replace />} />
        </Routes>
        <UnsavedBar />
      </>
    );
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end gap-4 border-b border-gray-200 pb-3">
        <div>
          <h2 className="flex items-center gap-2 text-xl font-bold text-gray-900">
            <Tags className="h-5 w-5" /> Metadata Manager
          </h2>
          <p className="mt-1 text-sm text-gray-500">Find a table, then change its description and tags.</p>
        </div>
        {ctx?.whoami.can_browse && (
          <nav aria-label="Metadata Manager" className="ml-auto flex items-center gap-1">
            <NavItem to={METADATA_BASE} end={false} matchBrowse>
              Browse
            </NavItem>
            <NavItem to={historyPath}>History</NavItem>
            {ctx.isAdmin && (
              <NavItem to={adminPath}>
                <Settings2 className="h-4 w-4" /> Sync &amp; admin
              </NavItem>
            )}
          </nav>
        )}
      </div>
      {ctx?.whoami.can_browse && !onAdmin && !location.pathname.startsWith(historyPath) && <Welcome />}
      {ctx ? (
        <MetadataContext.Provider value={ctx}>
          {body}
          {renderPanel(panel, () => setPanel(null), setPanel)}
        </MetadataContext.Provider>
      ) : (
        body
      )}
      {toast && <Toast toast={toast} onDismiss={dismissToast} />}
    </div>
  );
}

function NavItem({ to, children, end = true, matchBrowse }: { to: string; children: ReactNode; end?: boolean; matchBrowse?: boolean }) {
  const location = useLocation();
  const browseActive =
    matchBrowse && (location.pathname === METADATA_BASE || location.pathname.startsWith(`${METADATA_BASE}/browse`) || location.pathname.startsWith(datasetsPath));
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        cn(
          'inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium',
          (matchBrowse ? browseActive : isActive) ? 'bg-primary/10 text-primary' : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900',
        )
      }
    >
      {children}
    </NavLink>
  );
}

/** The catalog list, or where an old `?tab=` link pointed. */
function LegacyTabRedirect() {
  const [params] = useSearchParams();
  const tab = params.get('tab');
  if (tab === 'changes') return <Navigate to={historyPath} replace />;
  if (tab === 'drift' || tab === 'runs' || tab === 'principals') return <Navigate to={adminPath} replace />;
  return <BrowsePage />;
}

function Welcome() {
  const [dismissed, setDismissed] = useState(() => localStorage.getItem(WELCOME_KEY) === '1');
  if (dismissed) return null;
  const close = () => {
    localStorage.setItem(WELCOME_KEY, '1');
    setDismissed(true);
  };
  return (
    <section aria-label="How this works" className="relative rounded-lg border border-primary/20 bg-primary/5 p-4 pr-10 text-sm text-gray-700">
      <button type="button" onClick={close} className="absolute right-3 top-3 text-gray-400 hover:text-gray-700" aria-label="Dismiss">
        <X className="h-4 w-4" />
      </button>
      <p className="font-medium text-gray-900">How this works</p>
      <ol className="mt-1 list-decimal space-y-0.5 pl-5">
        <li>Open a catalog or a dataset, or search, to find a table.</li>
        <li>Change its description or tags right on the page. Your edits wait at the bottom until you save them in the next step.</li>
        <li>
          Click <span className="font-medium">Review and save</span> at the bottom. Some changes go to the owners for approval first.
        </li>
      </ol>
    </section>
  );
}

function renderPanel(panel: Panel | null, close: () => void, open: (p: Panel) => void): ReactNode {
  if (!panel) return null;
  switch (panel.kind) {
    case 'changeset':
      return <ChangeSetDrawer key={panel.id} id={panel.id} onClose={close} onRevert={(changeSet) => open({ kind: 'revert', changeSet })} />;
    case 'run':
      return <RunDrawer key={panel.id} id={panel.id} onClose={close} />;
    case 'revert':
      return <RevertDialog changeSet={panel.changeSet} onClose={close} />;
  }
}
