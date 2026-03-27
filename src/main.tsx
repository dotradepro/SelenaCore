import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './i18n/i18n';
import App from './App.tsx';
import './index.css';

// Kiosk mode: add class to <html> when ?kiosk=1 is in URL
if (new URLSearchParams(window.location.search).get('kiosk') === '1') {
  document.documentElement.classList.add('kiosk');
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
