import { useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { useStore } from './store/useStore';
import { useConnectionHealth } from './hooks/useConnectionHealth';
import Wizard from './components/Wizard';
import LanguageSelect from './components/LanguageSelect';
import Layout from './components/Layout';
import Dashboard from './components/Dashboard';
import Settings from './components/Settings';
import KioskElevationGate from './components/KioskElevationGate';

export default function App() {
  // Kiosk watchdog: force-reload if WebSocket sync is unresponsive > 60s
  useConnectionHealth();
  const isConfigured = useStore((state) => state.isConfigured);
  const wizardLoading = useStore((state) => state.wizardLoading);
  const setupStage = useStore((state) => state.setupStage);
  const wizardRequirements = useStore((state) => state.wizardRequirements);
  const setSetupStage = useStore((state) => state.setSetupStage);
  const fetchWizardStatus = useStore((state) => state.fetchWizardStatus);
  const fetchWizardRequirements = useStore((state) => state.fetchWizardRequirements);
  const connectSyncStream = useStore((state) => state.connectSyncStream);
  const initThemeListener = useStore((state) => state.initThemeListener);
  const fetchThemes = useStore((state) => state.fetchThemes);
  const fetchWallpapers = useStore((state) => state.fetchWallpapers);

  useEffect(() => {
    fetchWizardStatus();
    fetchWizardRequirements();
    fetchThemes();
    fetchWallpapers();
  }, [fetchWizardStatus, fetchWizardRequirements, fetchThemes, fetchWallpapers]);

  // While the wizard is being filled out we DO NOTHING in the background:
  //   no polling, no WebSocket sync, no wizardLoading flips.
  // Any background churn caused Wizard to unmount/remount, wiping the
  // user's selections mid-configuration. The wizard is a self-contained
  // form — until the user clicks "Finish" there is nothing to sync.
  //
  // Cross-tab handoff (wizard completed in another browser / on the
  // kiosk display while the operator finishes on a laptop) is handled
  // by a single cheap poll every 30s that reads BOTH /wizard/status
  // (drives `isConfigured`) and /wizard/requirements (drives
  // `canProceed`). App.tsx renders the Dashboard only when
  // `isConfigured && canProceed`; without refreshing requirements in
  // the poll, the kiosk stayed on the wizard screen after it was
  // completed elsewhere because canProceed remained stale from mount.
  // Neither of these fetches touches `wizardLoading`, so no spinner
  // flash and no remount.
  useEffect(() => {
    if (isConfigured) return;
    const id = setInterval(() => {
      fetchWizardStatus();
      fetchWizardRequirements();
    }, 30_000);
    return () => clearInterval(id);
  }, [isConfigured, fetchWizardStatus, fetchWizardRequirements]);

  // Apply theme and listen for system preference changes
  useEffect(() => {
    const cleanup = initThemeListener();
    return cleanup;
  }, [initThemeListener]);

  // Connect real-time sync stream (layout + module state) ONLY after the
  // wizard is done. During setup there's nothing to sync and every `hello`
  // payload was clobbering local UI state.
  useEffect(() => {
    if (!isConfigured) return;
    const disconnect = connectSyncStream();
    return disconnect;
  }, [isConfigured, connectSyncStream]);

  if (wizardLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ background: 'var(--bg)' }}>
        <div className="flex flex-col items-center gap-4" style={{ color: 'var(--tx2)' }}>
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
          {/* Dashboard — always accessible without auth */}
          <Route path="/" element={<Dashboard />} />

          {/* Settings (+ Modules/System/Integrity tabs) — require PIN/QR elevation */}
          <Route path="/settings/*" element={
            <KioskElevationGate><Settings /></KioskElevationGate>
          } />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  );
}
