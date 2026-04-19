import { useLocation, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { LayoutGrid, Zap, Mic, Home } from 'lucide-react';

/**
 * Mobile / narrow-viewport bottom navigation (v0.4.0 B1 complement).
 *
 * The kiosk (728px) uses the top-nav; this fallback renders only below
 * 768px where the top-bar gets too cramped to host horizontal links.
 * Layout.tsx mounts this via a CSS media query (see display rule).
 *
 * Items mirror the top-nav — same routes, same active-state rules —
 * so switching between the two is visually seamless.
 */
export default function BottomNavigation() {
    const { t } = useTranslation();
    const location = useLocation();
    const navigate = useNavigate();

    const items = [
        { path: '/', label: t('nav.dashboard'), Icon: Home, exact: true },
        { path: '/devices', label: t('nav.devices'), Icon: LayoutGrid, exact: false },
        { path: '/automations', label: t('nav.automations'), Icon: Zap, exact: false },
        { path: '/voice', label: t('nav.voice'), Icon: Mic, exact: false },
    ];

    const isActive = (path: string, exact: boolean) =>
        exact ? location.pathname === path : location.pathname.startsWith(path);

    return (
        <nav
            aria-label={t('nav.primary')}
            className="selena-bottom-nav"
            style={{
                display: 'none',  // toggled via CSS media query below
                position: 'fixed',
                bottom: 0,
                left: 0,
                right: 0,
                height: 56,
                background: 'var(--sf)',
                borderTop: '1px solid var(--b)',
                padding: '4px 8px',
                gap: 2,
                zIndex: 50,
            }}
        >
            {items.map(({ path, label, Icon, exact }) => {
                const active = isActive(path, exact);
                return (
                    <button
                        key={path}
                        onClick={() => navigate(path)}
                        aria-current={active ? 'page' : undefined}
                        style={{
                            flex: 1,
                            display: 'flex',
                            flexDirection: 'column',
                            alignItems: 'center',
                            justifyContent: 'center',
                            gap: 2,
                            padding: '4px 6px',
                            border: 'none',
                            borderRadius: 8,
                            background: 'transparent',
                            color: active ? 'var(--ac)' : 'var(--tx3)',
                            cursor: 'pointer',
                            transition: 'color .15s',
                        }}
                    >
                        <Icon size={20} />
                        <span style={{ fontSize: 10, fontWeight: 500, whiteSpace: 'nowrap' }}>
                            {label}
                        </span>
                    </button>
                );
            })}
        </nav>
    );
}
