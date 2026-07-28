import { useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { Layout } from './components/layout/Layout';
import { Home } from './pages/Home';
import { DataDiscovery } from './pages/DataDiscovery';
import { Requests } from './pages/Requests';
import { Admin } from './pages/Admin';
import { Approvals } from './pages/Approvals';
import { Training } from './pages/Training';
import { Events } from './pages/Events';
import { ReusableAssets } from './pages/ReusableAssets';
import { CommunityLinks } from './pages/CommunityLinks';
import { AdminReports } from './pages/AdminReports';
import { EmbeddedApp } from './pages/EmbeddedApp';
import { Welcome } from './pages/Welcome';
import { useBrandingStore } from './stores/brandingStore';
import { useRequestStore } from './stores/requestStore';
import { useUserStore } from './stores/userStore';
import { api } from './services/api';
import { reconcileCacheOwner } from './lib/chatPersistence';

import { ProtectedRoute } from './components/auth/ProtectedRoute';

import { Allowlist } from './pages/admin/Allowlist';
import { EnforcementSentinel } from './pages/admin/EnforcementSentinel';
import { DataCertification } from './pages/admin/DataCertification';
import { ODPS } from './pages/admin/ODPS';
import { TagManagement } from './pages/admin/TagManagement';
import { ContextCatalog } from './pages/admin/ContextCatalog';
import { Workflows } from './pages/admin/Workflows';
import { ToolRegistry } from './pages/admin/ToolRegistry';
import { TrainingAdmin } from './pages/admin/TrainingAdmin';

function App() {
  const fetchBannerMessage = useRequestStore((state) => state.fetchBannerMessage);
  const { fetchBranding, hasLoaded, uiTabs } = useBrandingStore();
  const fetchCurrentUser = useUserStore((state) => state.fetchCurrentUser);
  const hydrated = useUserStore((state) => state.hydrated);
  const currentUserEmail = useUserStore((state) => state.currentUser?.email);

  useEffect(() => {
    fetchBannerMessage();
    fetchBranding();
  }, [fetchBannerMessage, fetchBranding]);

  useEffect(() => {
    if (hydrated) {
      fetchCurrentUser();
      // Start assembling this user's agent context now. It involves a slow
      // identity-provider lookup, so doing it at boot means it is ready by the
      // time they type their first message instead of that message waiting on
      // it. Fire-and-forget: `warmUserContext` never throws, and a failure just
      // means the agent asks more questions.
      void api.warmUserContext();
    }
  }, [fetchCurrentUser, hydrated]);

  // Cached transcripts live in `localStorage`, which belongs to the browser
  // rather than to a user. As soon as we know who is actually signed in, drop
  // anything cached for somebody else — otherwise the next person on a shared
  // machine inherits the previous one's conversation, and their first message
  // would save it to the server under their own account. Reloading is the
  // simplest way to be sure no component is still rendering what we just
  // deleted; it terminates because the stamp now matches.
  useEffect(() => {
    if (currentUserEmail && reconcileCacheOwner(currentUserEmail)) {
      window.location.reload();
    }
  }, [currentUserEmail]);

  if (!hasLoaded) {
    return null; // Don't render anything until branding is loaded to prevent flash
  }

  return (
    <BrowserRouter>
      <Layout>
        <Routes>
          {/* Landing is always the unified chat. */}
          <Route path="/" element={<Home />} />
          <Route path="/welcome" element={<Welcome />} />
          <Route path="/request" element={<Home />} />
          {/* Legacy redirects: prior versions exposed these routes. */}
          <Route path="/home" element={<Navigate to="/request" replace />} />
          <Route path="/apps" element={<Navigate to="/request" replace />} />
          <Route path="/discovery" element={<DataDiscovery />} />
          <Route path="/requests" element={<Requests />} />
          <Route path="/requests/:requestId" element={<Requests />} />
          <Route path="/approvals" element={<Approvals />} />
          <Route path="/reports" element={<AdminReports />} />
          <Route
            path="/admin"
            element={<Navigate to="/admin/dashboard" replace />}
          />
          <Route
            path="/admin/:tab"
            element={
              <ProtectedRoute allowedPersonas={['Platform Admin']}>
                <Admin />
              </ProtectedRoute>
            }
          />
          {uiTabs?.allowlist !== false && (
            <Route
              path="/governance/allowlist"
              element={
                <ProtectedRoute allowedPersonas={['Platform Admin', 'Governance Admin']}>
                  <Allowlist />
                </ProtectedRoute>
              }
            />
          )}
          {uiTabs?.sentinel !== false && (
            <Route
              path="/governance/sentinel"
              element={
                <ProtectedRoute allowedPersonas={['Platform Admin', 'Governance Admin']}>
                  <EnforcementSentinel />
                </ProtectedRoute>
              }
            />
          )}
          {uiTabs?.certification !== false && (
            <Route
              path="/governance/certification"
              element={
                <ProtectedRoute allowedPersonas={['Platform Admin', 'Governance Admin']}>
                  <DataCertification />
                </ProtectedRoute>
              }
            />
          )}
          {uiTabs?.odps !== false && (
            <Route
              path="/governance/odps"
              element={
                <ProtectedRoute allowedPersonas={['Platform Admin', 'Governance Admin']}>
                  <ODPS />
                </ProtectedRoute>
              }
            />
          )}
          {uiTabs?.tag_management !== false && (
            <Route
              path="/governance/tags"
              element={
                <ProtectedRoute allowedPersonas={['Platform Admin', 'Governance Admin']}>
                  <TagManagement />
                </ProtectedRoute>
              }
            />
          )}
          {uiTabs?.context_catalog !== false && (
            <Route
              path="/governance/context-catalog"
              element={
                <ProtectedRoute allowedPersonas={['Platform Admin', 'Governance Admin']}>
                  <ContextCatalog />
                </ProtectedRoute>
              }
            />
          )}
          {uiTabs?.workflows !== false && (
            <Route
              path="/build/workflows"
              element={
                <ProtectedRoute allowedPersonas={['Platform Admin', 'Governance Admin']}>
                  <Workflows />
                </ProtectedRoute>
              }
            />
          )}
          {uiTabs?.tool_registry !== false && (
            <Route
              path="/build/tool-registry"
              element={
                <ProtectedRoute allowedPersonas={['Platform Admin', 'Governance Admin']}>
                  <ToolRegistry />
                </ProtectedRoute>
              }
            />
          )}
          {/* Back-compat: Tool Registry moved out of /governance/. */}
          <Route
            path="/governance/tool-registry"
            element={<Navigate to="/build/tool-registry" replace />}
          />
          {uiTabs?.training_admin !== false && (
            <Route
              path="/build/training"
              element={
                <ProtectedRoute allowedPersonas={['Platform Admin', 'Governance Admin']}>
                  <TrainingAdmin />
                </ProtectedRoute>
              }
            />
          )}
          {/* Generic config-driven iframe apps (embedded_apps in
              configuration.yaml). */}
          <Route path="/embedded/:appId" element={<EmbeddedApp />} />
          {/* Back-compat: the Command Center used to live at its own route;
              it's now just an embedded app with id `command_center`. */}
          <Route
            path="/command-center"
            element={<Navigate to="/embedded/command_center" replace />}
          />
          {/* Ask Your Data folded into the unified chat. */}
          <Route path="/ask-your-data" element={<Navigate to="/" replace />} />
          <Route path="/community/training" element={<Training />} />
          <Route path="/community/events" element={<Events />} />
          <Route path="/community/assets" element={<ReusableAssets />} />
          <Route path="/community/links" element={<CommunityLinks />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  );
}

export default App;
