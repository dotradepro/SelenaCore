import { useEffect, useRef } from 'react';
import { useStore } from '../store/useStore';

const UM = '/api/ui/modules/user-manager';
const HEARTBEAT_INTERVAL = 60_000;   // send heartbeat every 60s
const IDLE_CHECK_INTERVAL = 10_000;  // check idle state every 10s
const ACTIVITY_EVENTS = ['mousemove', 'mousedown', 'keydown', 'touchstart', 'scroll', 'click'] as const;

/** Clear all auth tokens and redirect to dashboard. */
function expireAndRedirect(token: string | null, initAuth: () => void) {
    sessionStorage.removeItem('selena_session');
    localStorage.removeItem('selena_device');
    document.cookie = 'selena_device=; max-age=0; path=/';
    if (token) {
        fetch(`${UM}/auth/session/logout`, {
            method: 'POST',
            headers: { 'X-Device-Token': token },
        }).catch(() => { });
    }
    initAuth();
    // Redirect to dashboard so user doesn't see AuthWall
    if (window.location.pathname !== '/') {
        window.location.href = '/';
    }
}

/**
 * Hook that keeps a temporary browser session alive by:
 * 1. Detecting user activity (mouse, keyboard, touch, scroll)
 * 2. Sending periodic heartbeats to the backend
 * 3. Auto-logging out when the session expires (no activity for 5 min)
 *
 * Only active when the user has a session token (QR login).
 * For persistent device tokens this hook does nothing.
 */
export function useSessionKeepAlive() {
    const initAuth = useStore((s) => s.initAuth);
    const lastActivityRef = useRef(Date.now());
    const isSessionRef = useRef(false);

    useEffect(() => {
        const token = sessionStorage.getItem('selena_session');
        if (!token) return;
        isSessionRef.current = true;

        // Track user activity
        const onActivity = () => { lastActivityRef.current = Date.now(); };
        for (const evt of ACTIVITY_EVENTS) {
            window.addEventListener(evt, onActivity, { passive: true });
        }

        // Heartbeat interval — only if there was recent activity
        const heartbeatId = setInterval(async () => {
            const token = sessionStorage.getItem('selena_session');
            if (!token) return;
            try {
                const res = await fetch(`${UM}/auth/session/heartbeat`, {
                    method: 'POST',
                    headers: { 'X-Device-Token': token },
                    credentials: 'include',
                });
                if (res.status === 401) {
                    // Session expired server-side
                    expireAndRedirect(token, initAuth);
                }
            } catch { /* network error — ignore */ }
        }, HEARTBEAT_INTERVAL);

        // Idle check — if no activity for the timeout, proactively logout
        const idleCheckId = setInterval(() => {
            const token = sessionStorage.getItem('selena_session');
            if (!token) return;
            const idle = Date.now() - lastActivityRef.current;
            // 5 minutes + 10s grace
            if (idle > 310_000) {
                expireAndRedirect(token, initAuth);
            }
        }, IDLE_CHECK_INTERVAL);

        return () => {
            for (const evt of ACTIVITY_EVENTS) {
                window.removeEventListener(evt, onActivity);
            }
            clearInterval(heartbeatId);
            clearInterval(idleCheckId);
        };
    }, [initAuth]);
}
