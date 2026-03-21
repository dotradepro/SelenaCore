import { useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { useStore } from './store/useStore';
import Wizard from './components/Wizard';
import Layout from './components/Layout';
import Dashboard from './components/Dashboard';
import Settings from './components/Settings';
import Modules from './components/Modules';
import Devices from './components/Devices';

export default function App() {
  const isConfigured = useStore((state) => state.isConfigured);
  const wizardLoading = useStore((state) => state.wizardLoading);
  const fetchWizardStatus = useStore((state) => state.fetchWizardStatus);

  useEffect(() => {
    fetchWizardStatus();
  }, [fetchWizardStatus]);

  if (wizardLoading) {
    return (
      <div className="min-h-screen bg-zinc-950 flex items-center justify-center">
        <div className="flex flex-col items-center gap-4 text-zinc-400">
          <div className="w-8 h-8 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin" />
          <span className="text-sm">Загрузка SelenaCore...</span>
        </div>
      </div>
    );
  }

  if (!isConfigured) {
    return <Wizard />;
  }

  return (
    <BrowserRouter>
      <Layout>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/devices" element={<Devices />} />
          <Route path="/modules" element={<Modules />} />
          <Route path="/settings/*" element={<Settings />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  );
}
