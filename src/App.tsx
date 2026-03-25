import { useEffect, type ReactNode } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
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
            <Route path="/" element={<Dashboard />} />
            <Route path="/modules" element={<Modules />} />
            <Route path="/modules/:name" element={<ModuleDetail />} />
            <Route path="/system" element={<SystemPage />} />
            <Route path="/integrity" element={<IntegrityPage />} />
            <Route path="/settings/*" element={<Settings />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Layout>
      </AuthGate>
    </BrowserRouter>
  );
}

// ─── AuthGate ──────────────────────────────────────────────────────────────────
// Wraps the entire app. Shows a spinner while auth is resolving,
// then shows AuthWall for unregistered browsers (unless running as kiosk).
function AuthGate({
  authLoading,
  user,
  children,
}: {
  authLoading: boolean;
  user: AuthUser | null;
  children: ReactNode;
}) {
  // Kiosk device (?kiosk=1) is always trusted — token comes from wizard
  const isAuthenticated = !authLoading && user?.authenticated === true;
  const isKiosk = new URLSearchParams(window.location.search).has('kiosk');

  if (authLoading) {
    return (
      <div className="fixed inset-0 flex items-center justify-center" style={{ background: 'var(--bg)' }}>
        <div className="w-6 h-6 border-2 border-violet-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (!isKiosk && !isAuthenticated) {
    return <AuthWall />;
  }

  return <>{children}</>;
}
