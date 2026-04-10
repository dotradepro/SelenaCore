import { useEffect, useRef } from 'react';
import { useStore } from '../store/useStore';

const STALE_THRESHOLD_MS = 60_000; // 60 seconds without server contact
const CHECK_INTERVAL_MS = 10_000;  // check every 10 seconds

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
      const elapsed = Date.now() - useStore.getState().lastServerContact;
      if (elapsed > STALE_THRESHOLD_MS) {
        window.location.reload();
      }
    }, CHECK_INTERVAL_MS);

    return () => clearInterval(interval);
  }, []);
}
