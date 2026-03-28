import { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useStore } from '../store/useStore';
import type { AuthUser } from '../store/useStore';

const INACTIVITY_MS = 5 * 60 * 1000; // 5 min — frontend debounce timer
const PING_MS       = 60 * 1000;     // ping backend every 1 min if user is active
const EVENTS        = ['click', 'keypress', 'mousemove', 'scroll'] as const;

const GUEST_USER: AuthUser = {
    name: 'Guest', role: 'guest', user_id: null, device_id: null, authenticated: false,
};

/**
 * Inactivity session lock for kiosk/elevated sessions.
 *
 * Frontend:
 *   - Listens to click/keypress/mousemove/scroll
 *   - On any event: clearTimeout + new setTimeout (debounce pattern)
 *   - On timeout: revoke elevated session + reset to guest + go to /
 *   - Ping backend every 1 min if there was local activity
 *
 * Backend:
 *   - /auth/elevated/refresh: resets server-side TTL (sliding window)
 *   - Returns 401 if session already expired → frontend clears immediately
 */
export function useKioskInactivity() {
    const elevatedToken    = useStore((s) => s.elevatedToken);
    const setElevatedToken = useStore((s) => s.setElevatedToken);
    const setUser          = useStore((s) => s.setUser);
    const navigate         = useNavigate();

    // Refs — stable values accessible inside the effect without re-running it
    const tokenRef  = useRef(elevatedToken);
    const cbRef     = useRef({ setElevatedToken, setUser, navigate });
    tokenRef.current = elevatedToken;
    cbRef.current    = { setElevatedToken, setUser, navigate };

    useEffect(() => {
        if (!elevatedToken) return;

        let hasActivity = false;

        // ── Timeout handler ─────────────────────────────────────────────────
        const handleTimeout = async () => {
            const tok = tokenRef.current;
            if (!tok) return;
            try {
                await fetch('/api/ui/modules/user-manager/auth/elevated/revoke', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ elevated_token: tok }),
                });
            } catch { /* best-effort */ }
            cbRef.current.setElevatedToken(null);
            cbRef.current.setUser(GUEST_USER);
            cbRef.current.navigate('/', { replace: true });
        };

        // ── Debounce timer (frontend) ────────────────────────────────────────
        let timer = setTimeout(handleTimeout, INACTIVITY_MS);

        const reset = () => {
            clearTimeout(timer);
            timer = setTimeout(handleTimeout, INACTIVITY_MS);
            hasActivity = true;
        };

        for (const ev of EVENTS) window.addEventListener(ev, reset, { passive: true });

        // ── Ping backend every 1 min if user was active ─────────────────────
        const pingInterval = setInterval(async () => {
            if (!hasActivity) return; // no local activity — skip ping
            hasActivity = false;

            const tok = tokenRef.current;
            if (!tok) return;

            try {
                const res = await fetch('/api/ui/modules/user-manager/auth/elevated/refresh', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ elevated_token: tok }),
                });
                if (res.status === 401) {
                    // Backend session expired independently (e.g. server restart)
                    clearTimeout(timer);
                    cbRef.current.setElevatedToken(null);
                    cbRef.current.setUser(GUEST_USER);
                    cbRef.current.navigate('/', { replace: true });
                }
            } catch { /* network error — keep session, retry next minute */ }
        }, PING_MS);

        return () => {
            clearTimeout(timer);
            clearInterval(pingInterval);
            for (const ev of EVENTS) window.removeEventListener(ev, reset);
        };
    }, [elevatedToken]); // ← only re-runs when token appears or disappears
}
