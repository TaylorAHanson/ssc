import { useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { Layout } from './components/layout/Layout';
import { Home } from './pages/Home';
import { Requests } from './pages/Requests';
import { Admin } from './pages/Admin';
import { Approvals } from './pages/Approvals';
import { Training } from './pages/Training';
import { Events } from './pages/Events';
import { ReusableAssets } from './pages/ReusableAssets';
import { CommunityLinks } from './pages/CommunityLinks';
import { DataAccessForm } from './pages/DataAccessForm';
import { WorkspaceAccessForm } from './pages/WorkspaceAccessForm';
import { ServicePrincipalForm } from './pages/ServicePrincipalForm';
import { ProvisionWorkspaceForm } from './pages/ProvisionWorkspaceForm';
import { CatalogSchemaTableForm } from './pages/CatalogSchemaTableForm';
import { RESTAPIForm } from './pages/RESTAPIForm';
import { BatchDataForm } from './pages/BatchDataForm';
import { MarketplaceForm } from './pages/MarketplaceForm';
import { GithubRepoCreationForm } from './pages/GithubRepoCreationForm';
import { useRequestStore } from './stores/requestStore';

function App() {
  const fetchBannerMessage = useRequestStore((state) => state.fetchBannerMessage);

  useEffect(() => {
    fetchBannerMessage();
  }, [fetchBannerMessage]);

  return (
    <BrowserRouter>
      <Layout>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/requests" element={<Requests />} />
          <Route path="/approvals" element={<Approvals />} />
          <Route path="/admin" element={<Admin />} />
          <Route path="/community/training" element={<Training />} />
          <Route path="/community/events" element={<Events />} />
          <Route path="/community/assets" element={<ReusableAssets />} />
          <Route path="/community/links" element={<CommunityLinks />} />
          {/* PAAS Routes - All go to SurveyJS forms */}
          <Route path="/paas/workspace-access" element={<WorkspaceAccessForm />} />
          <Route path="/paas/request-catalog" element={<CatalogSchemaTableForm />} />
          <Route path="/paas/request-access" element={<DataAccessForm />} />
          <Route path="/paas/provision-workspace" element={<ProvisionWorkspaceForm />} />
          <Route path="/paas/service-principal" element={<ServicePrincipalForm />} />
          <Route path="/paas/marketplace" element={<MarketplaceForm />} />
          <Route path="/paas/github-repo-creation" element={<GithubRepoCreationForm />} />
          {/* DAAS Routes - All go to SurveyJS forms */}
          <Route path="/daas/rest-api" element={<RESTAPIForm />} />
          <Route path="/daas/batch-data" element={<BatchDataForm />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  );
}

export default App;
