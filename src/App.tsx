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
import { useBrandingStore } from './stores/brandingStore';
import { useRequestStore } from './stores/requestStore';
import { useUserStore } from './stores/userStore';

import { ProtectedRoute } from './components/auth/ProtectedRoute';

import { Allowlist } from './pages/admin/Allowlist';
import { EnforcementSentinel } from './pages/admin/EnforcementSentinel';
import { DataCertification } from './pages/admin/DataCertification';
import { ODPS } from './pages/admin/ODPS';

function App() {
  const fetchBannerMessage = useRequestStore((state) => state.fetchBannerMessage);
  const { fetchBranding, hasLoaded } = useBrandingStore();
  const fetchCurrentUser = useUserStore((state) => state.fetchCurrentUser);
  const hydrated = useUserStore((state) => state.hydrated);

  useEffect(() => {
    fetchBannerMessage();
    fetchBranding();
    if (hydrated) {
      fetchCurrentUser();
    }
  }, [fetchBannerMessage, fetchBranding, fetchCurrentUser, hydrated]);

  if (!hasLoaded) {
    return null; // Don't render anything until branding is loaded to prevent flash
  }

  return (
    <BrowserRouter>
      <Layout>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/discovery" element={<DataDiscovery />} />
          <Route path="/requests" element={<Requests />} />
          <Route path="/requests/:requestId" element={<Requests />} />
          <Route path="/approvals" element={<Approvals />} />
          <Route path="/reports" element={<AdminReports />} />
          <Route
            path="/admin"
            element={
              <ProtectedRoute allowedPersonas={['Platform Admin']}>
                <Admin />
              </ProtectedRoute>
            }
          />
          <Route
            path="/governance/allowlist"
            element={
              <ProtectedRoute allowedPersonas={['Platform Admin', 'Governance Admin']}>
                <Allowlist />
              </ProtectedRoute>
            }
          />
          <Route
            path="/governance/sentinel"
            element={
              <ProtectedRoute allowedPersonas={['Platform Admin', 'Governance Admin']}>
                <EnforcementSentinel />
              </ProtectedRoute>
            }
          />
          <Route
            path="/governance/certification"
            element={
              <ProtectedRoute allowedPersonas={['Platform Admin', 'Governance Admin']}>
                <DataCertification />
              </ProtectedRoute>
            }
          />
          <Route
            path="/governance/odps"
            element={
              <ProtectedRoute allowedPersonas={['Platform Admin', 'Governance Admin']}>
                <ODPS />
              </ProtectedRoute>
            }
          />
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
