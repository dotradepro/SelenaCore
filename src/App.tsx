import { useEffect, type ReactNode } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { useStore } from './store/useStore';
import type { AuthUser } from './store/useStore';
import Wizard from './components/Wizard';
import LanguageSelect from './components/LanguageSelect';
import Layout from './components/Layout';
import Dashboard from './components/Dashboard';
import Modules from './components/Modules';
import ModuleDetail from './components/ModuleDetail';
import SystemPage from './components/SystemPage';
import IntegrityPage from './components/IntegrityPage';
import Settings from './components/Settings';
import AuthWall from './components/AuthWall';
import KioskElevationGate from './components/KioskElevationGate';
import { useSessionKeepAlive } from './hooks/useSessionKeepAlive';
import { useKioskInactivity } from './hooks/useKioskInactivity';
// All browser sessions require elevation for restricted routes —
// there are no "trusted" browsers, only registered devices (for push/auth).
const isKiosk = true;

export default function App() {
  const isConfigured = useStore((state) => state.isConfigured);
  const wizardLoading = useStore((state) => state.wizardLoading);
  const setupStage = useStore((state) => state.setupStage);
  const wizardRequirements = useStore((state) => state.wizardRequirements);
  const setSetupStage = useStore((state) => state.setSetupStage);
  const fetchWizardStatus = useStore((state) => state.fetchWizardStatus);
  const fetchWizardRequirements = useStore((state) => state.fetchWizardRequirements);
  const connectSyncStream = useStore((state) => state.connectSyncStream);
  const initThemeListener = useStore((state) => state.initThemeListener);
  const initAuth = useStore((state) => state.initAuth);
  const user = useStore((state) => state.user);
  const authLoading = useStore((state) => state.authLoading);

  useEffect(() => {
    fetchWizardStatus();
    fetchWizardRequirements();
  }, [fetchWizardStatus, fetchWizardRequirements]);

  // Initialise device-token auth (fire-and-forget — non-blocking)
  useEffect(() => {
    initAuth();
  }, [initAuth]);

  // Keep temporary browser sessions alive (heartbeat + idle check)
  useSessionKeepAlive();

  // Apply theme and listen for system preference changes
  useEffect(() => {
    const cleanup = initThemeListener();
    return cleanup;
  }, [initThemeListener]);

  // Connect real-time sync stream (layout + module state) once on mount
  useEffect(() => {
    const disconnect = connectSyncStream();
    return disconnect;
  }, [connectSyncStream]);

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
      <AuthGate authLoading={authLoading} user={user}>
        <Layout>
          <Routes>
            {/* Dashboard — always accessible (even in kiosk without auth) */}
            <Route path="/" element={<Dashboard />} />

            {/* Restricted routes — require elevation in kiosk mode */}
            <Route path="/modules" element={
              isKiosk ? <KioskElevationGate><Modules /></KioskElevationGate> : <Modules />
            } />
            <Route path="/modules/:name" element={
              isKiosk ? <KioskElevationGate><ModuleDetail /></KioskElevationGate> : <ModuleDetail />
            } />
            <Route path="/system" element={
              isKiosk ? <KioskElevationGate><SystemPage /></KioskElevationGate> : <SystemPage />
            } />
            <Route path="/integrity" element={
              isKiosk ? <KioskElevationGate><IntegrityPage /></KioskElevationGate> : <IntegrityPage />
            } />
            <Route path="/settings/*" element={
              isKiosk ? <KioskElevationGate><Settings /></KioskElevationGate> : <Settings />
            } />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Layout>
      </AuthGate>
    </BrowserRouter>
  );
}

// ─── AuthGate ──────────────────────────────────────────────────────────────────
// Dashboard (/) is always accessible in guest mode.
// Restricted routes show AuthWall when not authenticated.
function AuthGate({
  authLoading,
  user,
  children,
}: {
  authLoading: boolean;
  user: AuthUser | null;
  children: ReactNode;
}) {
  const location = useLocation();
  const isAuthenticated = !authLoading && user?.authenticated === true;
  const isDashboard = location.pathname === '/';

  // Kiosk: lock elevated session after 5 min of inactivity
  useKioskInactivity();

  if (authLoading) {
    return (
      <div className="fixed inset-0 flex items-center justify-center" style={{ background: 'var(--bg)' }}>
        <div className="w-6 h-6 border-2 border-violet-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  // Dashboard is always accessible — even without auth (guest mode)
  // Restricted pages: show AuthWall so user can scan QR / enter PIN
  if (!isAuthenticated && !isDashboard) {
    return <AuthWall />;
  }

  return <>{children}</>;
}
