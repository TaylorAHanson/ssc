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
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  );
}

export default App;
