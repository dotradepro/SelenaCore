import { useEffect, useRef } from 'react';
import { useStore } from '../store/useStore';

// 5 minutes without server contact before forcing a page reload. 60s was
// too aggressive on Pi 4 — external catalog fetches (HuggingFace / Vosk)
// can block the WS message loop briefly, and any reload mid-wizard that
// fired caused users to lose form state on steps 5 (STT catalog) and 6
// (TTS catalog). Server-side pings run every 5s so a legitimately broken
// connection still gets caught within one threshold window.
const STALE_THRESHOLD_MS = 300_000;
const CHECK_INTERVAL_MS = 15_000;  // check every 15 seconds

/**
 * Kiosk watchdog: forces page reload if the WebSocket sync connection
 * has been unresponsive for longer than STALE_THRESHOLD_MS.
 *
 * Reads lastServerContact via getState() (non-reactive) to avoid
 * re-rendering the entire component tree on every WebSocket message.
 */
export function useConnectionHealth() {
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;

    // Only active on kiosk (localhost / 127.0.0.1 / ?kiosk=1)
    const host = window.location.hostname;
    const isKiosk =
      host === 'localhost' ||
      host === '127.0.0.1' ||
      new URLSearchParams(window.location.search).has('kiosk');
    if (!isKiosk) return;

    const interval = setInterval(() => {
      const state = useStore.getState();
      // Never force-reload while the wizard is still being filled out —
      // any reload mid-wizard lost selections and ruined UX. Post-wizard
      // (isConfigured=true) the watchdog remains useful to recover from
      // kiosk desyncs.
      if (!state.isConfigured) return;
      const elapsed = Date.now() - state.lastServerContact;
      if (elapsed > STALE_THRESHOLD_MS) {
        window.location.reload();
      }
    }, CHECK_INTERVAL_MS);

    return () => clearInterval(interval);
  }, []);
}
