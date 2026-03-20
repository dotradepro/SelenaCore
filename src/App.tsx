import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { useStore } from './store/useStore';
import Wizard from './components/Wizard';
import Layout from './components/Layout';
import Dashboard from './components/Dashboard';
import Settings from './components/Settings';
import Modules from './components/Modules';

export default function App() {
  const isConfigured = useStore((state) => state.isConfigured);

  if (!isConfigured) {
    return <Wizard />;
  }

  return (
    <BrowserRouter>
      <Layout>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/modules" element={<Modules />} />
          <Route path="/settings/*" element={<Settings />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  );
}
