import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './i18n/i18n';
import App from './App.tsx';
import './index.css';

// Kiosk cursor: hide completely when ?mouse=0 (no mouse connected)
if (new URLSearchParams(window.location.search).get('mouse') === '0') {
  document.documentElement.classList.add('kiosk-nomouse');
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
