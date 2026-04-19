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
import ModulePage from './components/ModulePage';

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

  // Cross-device wizard handoff (e.g. operator finishes the wizard on a
  // laptop while the kiosk display is still showing the wizard) is
  // delivered via SSE at `/api/ui/wizard/events` instead of polling.
  // Cog/WPE WebKit throttles background timers aggressively and a 30s
  // poll routinely misses the flip for minutes; the SSE push lands in
  // ~200 ms regardless of focus state. Connect only while not yet
  // configured and auto-close on the first `completed` event.
  useEffect(() => {
    if (isConfigured) return;
    const es = new EventSource('/api/ui/wizard/events');
    es.addEventListener('completed', () => {
      // Re-fetch both endpoints so the dashboard gate (isConfigured &&
      // canProceed) flips together — skipping requirements kept the
      // kiosk on the wizard page after a PC-side completion.
      fetchWizardStatus();
      fetchWizardRequirements();
      es.close();
    });
    return () => es.close();
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

  // Dashboard appears when the wizard is completed on the backend,
  // period. `canProceed` is for progress-within-wizard UX, not a
  // dashboard gate. Previously we gated on BOTH (`!isConfigured ||
  // !canProceed`), which meant a kiosk that finished /wizard/status
  // BEFORE /wizard/requirements resolved rendered the wizard component
  // and the Wizard's persisted-step localStorage froze it on the last
  // seen step — even when requirements eventually arrived. Single
  // source of truth = `isConfigured`.
  const shouldBlock = !isConfigured;

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

          {/* Top-level module pages promoted from /settings/system-modules
              in v0.4.0. Still PIN-gated: these expose device control and
              voice settings, same threat model as the old location. */}
          <Route path="/devices" element={
            <KioskElevationGate>
              <ModulePage moduleName="device-control" titleKey="nav.devices" />
            </KioskElevationGate>
          } />
          <Route path="/automations" element={
            <KioskElevationGate>
              <ModulePage moduleName="automation-engine" titleKey="nav.automations" />
            </KioskElevationGate>
          } />
          <Route path="/voice" element={
            <KioskElevationGate>
              <ModulePage moduleName="voice-core" titleKey="nav.voice" />
            </KioskElevationGate>
          } />

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
