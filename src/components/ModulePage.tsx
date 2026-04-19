import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

interface Props {
    /** Manifest name of the system module (kebab-case, e.g. "voice-core"). */
    moduleName: string;
    /** i18n key for the page title (e.g. "nav.voice"). */
    titleKey?: string;
}

/**
 * Top-level page that renders a single system module's settings iframe.
 *
 * v0.4.0 nav redesign (B1): previously these lived at
 * `/settings/system-modules` behind a picker; promoting them to
 * first-class nav items (`/voice`, `/devices`, `/automations`) shaves
 * 2-3 clicks off the most frequently used screens without discarding
 * the picker — Settings → System Modules stays for advanced modules.
 *
 * Keeps the same `/api/ui/modules/{name}/settings` URL so iframe
 * caching + the module's own load logic are unchanged.
 */
export default function ModulePage({ moduleName, titleKey }: Props) {
    const { t } = useTranslation();
    // Cache-bust key only on mount — avoids reload loops if the parent
    // re-renders for unrelated reasons (e.g. store updates).
    const [cacheBust] = useState(() => Date.now());
    const src = `/api/ui/modules/${moduleName}/settings?_=${cacheBust}`;

    // Broadcast language changes into the iframe the same way Settings ->
    // System Modules does (via postMessage lang_changed).
    useEffect(() => {
        const handler = () => {
            document.querySelectorAll('iframe').forEach(f => {
                try { f.contentWindow?.postMessage({ type: 'lang_changed' }, '*'); }
                catch { /* cross-origin quirks */ }
            });
        };
        window.addEventListener('storage', handler);
        return () => window.removeEventListener('storage', handler);
    }, []);

    return (
        <div style={{
            height: '100%', minHeight: 0,
            display: 'flex', flexDirection: 'column',
            padding: 12, gap: 12,
        }}>
            {titleKey && (
                <div style={{ flexShrink: 0 }}>
                    <h2 style={{ fontSize: 20, fontWeight: 600, margin: 0, color: 'var(--tx)' }}>
                        {t(titleKey)}
                    </h2>
                </div>
            )}

            <div style={{
                flex: 1, minHeight: 0,
                background: 'var(--sf)', border: '1px solid var(--b)',
                borderRadius: 12, overflow: 'hidden',
            }}>
                <iframe
                    src={src}
                    style={{ width: '100%', height: '100%', border: 'none', display: 'block' }}
                    title={moduleName}
                    allow="geolocation; microphone"
                />
            </div>
        </div>
    );
}
