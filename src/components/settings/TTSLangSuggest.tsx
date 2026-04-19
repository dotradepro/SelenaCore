import { useEffect, useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';

interface Voice {
    id: string;
    language: string;
    installed: boolean;
    active?: boolean;
}

interface Props {
    /** Currently selected UI language (e.g. "pl", "uk"). */
    currentLang: string;
    /** Parent toast channel — we emit human-readable status strings here. */
    onToast?: (msg: string) => void;
}

const DISMISS_KEY = (uiLang: string, voice: string) =>
    `selena-tts-suggest-dismissed:${uiLang}:${voice}`;
const OPT_OUT_KEY = 'selena-tts-suggest-opted-out';

function isPairDismissed(uiLang: string, voice: string): boolean {
    try { return localStorage.getItem(DISMISS_KEY(uiLang, voice)) === '1'; }
    catch { return false; }
}
function markPairDismissed(uiLang: string, voice: string) {
    try { localStorage.setItem(DISMISS_KEY(uiLang, voice), '1'); } catch { /* ignore */ }
}
function isOptedOut(): boolean {
    try { return localStorage.getItem(OPT_OUT_KEY) === '1'; }
    catch { return false; }
}

/**
 * Non-blocking suggestion banner shown under the LanguagePicker when the
 * active TTS primary voice doesn't match the UI language. Offers one-click
 * activation if a matching voice is already installed; otherwise points
 * the user at voice downloads.
 *
 * Silenced in three ways:
 *   • Current (ui_lang, tts_voice) pair has been dismissed (localStorage)
 *   • Global opt-out (Settings → Appearance toggle, localStorage)
 *   • Network / API failure (no surface — fails quiet)
 */
export default function TTSLangSuggest({ currentLang, onToast }: Props) {
    const { t } = useTranslation();
    const [primaryLang, setPrimaryLang] = useState<string | null>(null);
    const [primaryVoice, setPrimaryVoice] = useState<string | null>(null);
    const [candidateVoice, setCandidateVoice] = useState<Voice | null>(null);
    const [hidden, setHidden] = useState(false);
    const [activating, setActivating] = useState(false);

    const load = useCallback(async () => {
        if (isOptedOut()) { setHidden(true); return; }
        try {
            const [dualRes, voicesRes] = await Promise.all([
                fetch('/api/ui/setup/tts/dual-status').then(r => r.ok ? r.json() : null),
                fetch('/api/ui/setup/tts/voices').then(r => r.ok ? r.json() : null),
            ]);
            const pLang = dualRes?.primary?.lang ?? null;
            const pVoice = dualRes?.primary?.voice ?? null;
            setPrimaryLang(pLang);
            setPrimaryVoice(pVoice);

            if (!pLang || !pVoice) { setHidden(true); return; }
            if (pLang === currentLang) { setHidden(true); return; }
            if (isPairDismissed(currentLang, pVoice)) { setHidden(true); return; }

            const voices: Voice[] = voicesRes?.voices ?? [];
            // Prefer an installed voice matching the new UI lang; otherwise
            // return the first remote voice we could offer to download.
            const installedMatch = voices.find(v => v.language === currentLang && v.installed);
            const remoteMatch = voices.find(v => v.language === currentLang && !v.installed);
            setCandidateVoice(installedMatch ?? remoteMatch ?? null);
            setHidden(false);
        } catch {
            setHidden(true);
        }
    }, [currentLang]);

    useEffect(() => { load(); }, [load]);

    if (hidden || !primaryLang || !primaryVoice || primaryLang === currentLang) {
        return null;
    }

    const dismiss = () => {
        markPairDismissed(currentLang, primaryVoice);
        setHidden(true);
    };

    const activate = async () => {
        if (!candidateVoice?.installed) return;
        setActivating(true);
        try {
            const r = await fetch('/api/ui/setup/tts/select', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ voice: candidateVoice.id }),
            });
            if (r.ok) {
                onToast?.(t('ttsSuggest.activated', { voice: candidateVoice.id }));
                setHidden(true);
            } else {
                onToast?.(t('ttsSuggest.activateFailed'));
            }
        } catch {
            onToast?.(t('ttsSuggest.activateFailed'));
        } finally {
            setActivating(false);
        }
    };

    return (
        <div
            role="status"
            style={{
                marginTop: 16,
                padding: 14,
                borderRadius: 10,
                background: 'rgba(79, 140, 247, 0.06)',
                border: '1px solid rgba(79, 140, 247, 0.22)',
                color: 'var(--tx)',
                fontSize: 13,
                lineHeight: 1.45,
            }}
        >
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
                <span aria-hidden style={{ fontSize: 18, lineHeight: 1, flexShrink: 0, color: 'var(--ac)' }}>ℹ</span>
                <div style={{ flex: 1 }}>
                    <div style={{ fontWeight: 500, marginBottom: 4 }}>
                        {t('ttsSuggest.title')}
                    </div>
                    <div style={{ color: 'var(--tx2)', fontSize: 12 }}>
                        {t('ttsSuggest.body', {
                            ui: currentLang.toUpperCase(),
                            tts: primaryLang.toUpperCase(),
                        })}
                    </div>

                    {candidateVoice?.installed && (
                        <div style={{ marginTop: 10, fontSize: 12, color: 'var(--tx2)' }}>
                            • <code style={{ background: 'var(--sf2)', padding: '1px 6px', borderRadius: 4, fontSize: 11 }}>
                                {candidateVoice.id}
                            </code> {t('ttsSuggest.installed')}
                        </div>
                    )}
                    {candidateVoice && !candidateVoice.installed && (
                        <div style={{ marginTop: 10, fontSize: 12, color: 'var(--tx3)' }}>
                            {t('ttsSuggest.needsDownload', { voice: candidateVoice.id })}
                        </div>
                    )}
                    {!candidateVoice && (
                        <div style={{ marginTop: 10, fontSize: 12, color: 'var(--tx3)' }}>
                            {t('ttsSuggest.noMatch', { lang: currentLang })}
                        </div>
                    )}

                    <div style={{ marginTop: 12, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                        {candidateVoice?.installed && (
                            <button
                                onClick={activate}
                                disabled={activating}
                                style={{
                                    padding: '6px 14px',
                                    borderRadius: 6,
                                    border: '1px solid var(--ac)',
                                    background: 'var(--ac)',
                                    color: 'var(--on-accent, #fff)',
                                    fontSize: 12,
                                    fontWeight: 500,
                                    cursor: activating ? 'wait' : 'pointer',
                                    opacity: activating ? 0.6 : 1,
                                }}
                            >
                                {activating ? t('ttsSuggest.activating') : t('ttsSuggest.activate')}
                            </button>
                        )}
                        <button
                            onClick={dismiss}
                            style={{
                                padding: '6px 14px',
                                borderRadius: 6,
                                border: '1px solid var(--b)',
                                background: 'var(--sf2)',
                                color: 'var(--tx)',
                                fontSize: 12,
                                cursor: 'pointer',
                            }}
                        >
                            {t('ttsSuggest.keep')}
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );
}

/**
 * Tiny opt-out checkbox for Settings → Appearance. Toggling writes the
 * global flag that TTSLangSuggest checks on every mount.
 */
export function TTSLangSuggestOptOut() {
    const { t } = useTranslation();
    const [disabled, setDisabled] = useState<boolean>(isOptedOut());

    const toggle = () => {
        const next = !disabled;
        setDisabled(next);
        try {
            if (next) localStorage.setItem(OPT_OUT_KEY, '1');
            else localStorage.removeItem(OPT_OUT_KEY);
        } catch { /* ignore */ }
    };

    return (
        <label
            style={{
                display: 'flex', alignItems: 'center', gap: 10,
                cursor: 'pointer',
                fontSize: 12, color: 'var(--tx2)',
                marginTop: 12,
            }}
        >
            <input
                type="checkbox"
                checked={disabled}
                onChange={toggle}
                style={{ accentColor: 'var(--ac)' }}
            />
            {t('ttsSuggest.optOutLabel')}
        </label>
    );
}
