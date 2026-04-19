import { useLocation, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

/**
 * Top-bar horizontal navigation (v0.4.0 B1 redesign).
 *
 * Targets:
 *   - Dashboard (`/`) — the home screen
 *   - Devices (`/devices`) — device-control module
 *   - Automations (`/automations`) — automation-engine module
 *   - Voice (`/voice`) — voice-core module
 *
 * Lives between the logo and the right-side status cluster. The main
 * Settings / bell / clock / integrity-dot cluster stays untouched on
 * the right — this is additive, not a replacement.
 *
 * Hidden below the mobile breakpoint (<768px) — a bottom-nav variant
 * is planned there, but the target platform is a 728px kiosk so the
 * horizontal layout is what we're optimizing for.
 */
export default function TopNavigation() {
    const { t } = useTranslation();
    const location = useLocation();
    const navigate = useNavigate();

    const items = [
        { path: '/', label: t('nav.dashboard', 'Dashboard'), exact: true },
        { path: '/devices', label: t('nav.devices', 'Devices'), exact: false },
        { path: '/automations', label: t('nav.automations', 'Automations'), exact: false },
        { path: '/voice', label: t('nav.voice', 'Voice'), exact: false },
    ];

    const isActive = (path: string, exact: boolean) =>
        exact ? location.pathname === path : location.pathname.startsWith(path);

    return (
        <nav
            aria-label={t('nav.primary', 'Primary navigation')}
            style={{
                display: 'flex',
                alignItems: 'center',
                gap: 2,
                // Hide on narrow viewports — parent page uses a bottom-nav
                // fallback there.
                minWidth: 0,
            }}
        >
            {items.map((it) => {
                const active = isActive(it.path, it.exact);
                return (
                    <button
                        key={it.path}
                        onClick={() => navigate(it.path)}
                        aria-current={active ? 'page' : undefined}
                        style={{
                            padding: '6px 12px',
                            borderRadius: 8,
                            border: 'none',
                            background: active ? 'var(--sf3)' : 'transparent',
                            color: active ? 'var(--ac)' : 'var(--tx2)',
                            fontSize: 12,
                            fontWeight: active ? 500 : 400,
                            cursor: 'pointer',
                            transition: 'background .15s, color .15s',
                            whiteSpace: 'nowrap',
                        }}
                        onMouseEnter={(e) => {
                            if (!active) e.currentTarget.style.color = 'var(--tx)';
                        }}
                        onMouseLeave={(e) => {
                            if (!active) e.currentTarget.style.color = 'var(--tx2)';
                        }}
                    >
                        {it.label}
                    </button>
                );
            })}
        </nav>
    );
}
