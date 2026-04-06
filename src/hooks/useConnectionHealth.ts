import { useEffect } from 'react';
import { useStore } from '../store/useStore';

const STALE_THRESHOLD_MS = 60_000; // 60 seconds without server contact
const CHECK_INTERVAL_MS = 10_000;  // check every 10 seconds

/**
 * Kiosk watchdog: forces page reload if the WebSocket sync connection
 * has been unresponsive for longer than STALE_THRESHOLD_MS.
 *
 * This ensures the kiosk display never shows stale state even if
 * the WebSocket client has a bug or the network is unstable.
 */
export function useConnectionHealth() {
  const lastServerContact = useStore((s) => s.lastServerContact);

  useEffect(() => {
    const interval = setInterval(() => {
      const elapsed = Date.now() - lastServerContact;
      if (elapsed > STALE_THRESHOLD_MS) {
        window.location.reload();
      }
    }, CHECK_INTERVAL_MS);

    return () => clearInterval(interval);
  }, [lastServerContact]);
}
