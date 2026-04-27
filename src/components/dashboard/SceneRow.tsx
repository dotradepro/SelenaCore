import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useStore } from '../../store/useStore';

interface Scene {
  id: number;
  name_user: string;
  name_en: string;
  enabled: boolean;
}

interface ScenesResponse {
  scenes: Scene[];
}

/** Resolve the device bearer token using the same precedence as
 *  ``useStore.initAuth``: QR-session > cookie > localStorage fallback.
 *  Returns null if no token is present (the dashboard then falls back
 *  to silent failure — the row hides itself). */
function resolveDeviceToken(): string | null {
  if (typeof window === 'undefined') return null;
  try {
    const session = sessionStorage.getItem('selena_session');
    if (session) return session;
  } catch { /* ignore */ }
  try {
    const m = document.cookie.match(/(?:^|;\s*)selena_device=([^;]+)/);
    if (m?.[1]) return decodeURIComponent(m[1]);
  } catch { /* ignore */ }
  try {
    return localStorage.getItem('selena_device');
  } catch { return null; }
}

function authHeader(token: string | null): HeadersInit {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export default function SceneRow() {
  const { t, i18n } = useTranslation();
  const elevatedToken = useStore((s) => s.elevatedToken);
  const token = elevatedToken || resolveDeviceToken();

  const [scenes, setScenes] = useState<Scene[] | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    fetch('/api/v1/scenes?enabled_only=true', { headers: authHeader(token) })
      .then((r) => (r.ok ? (r.json() as Promise<ScenesResponse>) : null))
      .then((data) => { if (alive && data) setScenes(data.scenes); })
      .catch(() => { if (alive) setScenes([]); });
    return () => { alive = false; };
  }, [token]);

  // Hide the row entirely on a fresh install with no defined scenes.
  if (!scenes || scenes.length === 0) return null;

  const showToast = useStore.getState().showToast;

  async function activate(scene: Scene) {
    if (busy !== null) return;
    setBusy(scene.id);
    try {
      const r = await fetch(`/api/v1/scenes/${scene.id}/activate`, {
        method: 'POST',
        headers: { ...authHeader(token), 'Content-Type': 'application/json' },
      });
      if (!r.ok) throw new Error(await r.text());
      const data = await r.json();
      const failed = data.actions_failed ?? 0;
      if (failed > 0) {
        showToast(t('dashboardV2.scenes.partial', { name: sceneLabel(scene, i18n.language) }), 'info');
      } else {
        showToast(t('dashboardV2.scenes.activated', { name: sceneLabel(scene, i18n.language) }), 'success');
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'failed';
      showToast(t('dashboardV2.scenes.failed', { name: sceneLabel(scene, i18n.language), error: msg }), 'error');
    } finally {
      setBusy(null);
    }
  }

  return (
    <div
      style={{
        display: 'flex',
        gap: 8,
        padding: '0 4px 10px',
        overflowX: 'auto',
        scrollbarWidth: 'none',
      }}
    >
      {scenes.map((s) => (
        <button
          key={s.id}
          onClick={() => activate(s)}
          disabled={busy !== null}
          style={{
            flex: '0 0 auto',
            minWidth: 110,
            padding: '8px 12px',
            borderRadius: 12,
            background: 'var(--sf)',
            border: '1px solid var(--b)',
            color: 'var(--tx)',
            fontSize: 12,
            fontWeight: 500,
            cursor: busy !== null ? 'wait' : 'pointer',
            opacity: busy === s.id ? 0.6 : 1,
            transition: 'background .12s, border-color .12s',
          }}
        >
          <span aria-hidden style={{ marginRight: 6 }}>
            {iconFor(s)}
          </span>
          {sceneLabel(s, i18n.language)}
        </button>
      ))}
    </div>
  );
}

/** Pick the user-facing scene name, preferring the localized one. */
function sceneLabel(scene: Scene, lang: string): string {
  if (lang.startsWith('en') && scene.name_en) return scene.name_en;
  return scene.name_user || scene.name_en;
}

/** Cheap heuristic: pick an emoji from the english scene name. We don't
 *  ship an icon library yet; once lucide-react is wired into V2 chrome we
 *  swap this for a proper component. */
function iconFor(s: Scene): string {
  const n = (s.name_en || s.name_user || '').toLowerCase();
  if (n.includes('morning') || n.includes('wake')) return '☀️';
  if (n.includes('night') || n.includes('sleep') || n.includes('bed')) return '🌙';
  if (n.includes('movie') || n.includes('cinema') || n.includes('tv')) return '🎬';
  if (n.includes('away') || n.includes('leav')) return '👋';
  if (n.includes('home') || n.includes('arriv')) return '🏠';
  return '✦';
}
