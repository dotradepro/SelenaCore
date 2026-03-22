import { useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { useStore } from './store/useStore';
import Wizard from './components/Wizard';
import LanguageSelect from './components/LanguageSelect';
import Layout from './components/Layout';
import Dashboard from './components/Dashboard';
import Modules from './components/Modules';
import ModuleDetail from './components/ModuleDetail';
import SystemPage from './components/SystemPage';
import IntegrityPage from './components/IntegrityPage';
import Settings from './components/Settings';

// Hide cursor when running on kiosk device (?kiosk=1)
const isKiosk = new URLSearchParams(window.location.search).has('kiosk');
if (isKiosk) {
  const style = document.createElement('style');
  style.textContent = 'html, html * { cursor: none !important; }';
  document.head.appendChild(style);
}

export default function App() {
  const isConfigured = useStore((state) => state.isConfigured);
  const wizardLoading = useStore((state) => state.wizardLoading);
  const setupStage = useStore((state) => state.setupStage);
  const wizardRequirements = useStore((state) => state.wizardRequirements);
  const setSetupStage = useStore((state) => state.setSetupStage);
  const fetchWizardStatus = useStore((state) => state.fetchWizardStatus);
  const fetchWizardRequirements = useStore((state) => state.fetchWizardRequirements);
  const connectSyncStream = useStore((state) => state.connectSyncStream);

  useEffect(() => {
    fetchWizardStatus();
    fetchWizardRequirements();
  }, [fetchWizardStatus, fetchWizardRequirements]);

  // Connect real-time sync stream (layout + module state) once on mount
  useEffect(() => {
    const disconnect = connectSyncStream();
    return disconnect;
  }, [connectSyncStream]);

  if (wizardLoading) {
    return (
      <div className="min-h-screen bg-black flex items-center justify-center">
        <div className="flex flex-col items-center gap-4 text-zinc-400">
          <div className="w-8 h-8 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin" />
        </div>
      </div>
    );
  }

  const canProceed = wizardRequirements?.can_proceed ?? false;
  const shouldBlock = !isConfigured || !canProceed;

  if (shouldBlock) {
    if (setupStage === 'wizard') {
      return <Wizard />;
    }
    return (
      <LanguageSelect
        onContinue={() => setSetupStage('wizard')}
      />
    );
  }

  return (
    <BrowserRouter>
      <Layout>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/modules" element={<Modules />} />
          <Route path="/modules/:name" element={<ModuleDetail />} />
          <Route path="/system" element={<SystemPage />} />
          <Route path="/integrity" element={<IntegrityPage />} />
          <Route path="/settings/*" element={<Settings />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  );
}
