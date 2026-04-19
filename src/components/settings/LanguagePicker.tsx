import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import languagesManifest from '../../i18n/languages.json';
import LanguageBanner from './LanguageBanner';

export interface LanguageOption {
    code: string;
    nativeName: string;
    direction: 'ltr' | 'rtl';
    quality: 'manual' | 'auto';
}

interface Props {
    currentLang: string;
    onChange: (code: string) => void;
}

const BANNER_SEEN_KEY_PREFIX = 'selena-autolang-banner-seen:';

function hasSeenBanner(code: string): boolean {
    try { return localStorage.getItem(BANNER_SEEN_KEY_PREFIX + code) === '1'; }
    catch { return false; }
}

function markBannerSeen(code: string): void {
    try { localStorage.setItem(BANNER_SEEN_KEY_PREFIX + code, '1'); } catch { /* ignore */ }
}

/**
 * Settings → Appearance → Language picker (v0.4.0 redesign).
 *
 * Full list with search filter (plan Variant 2) — better for 7" kiosk
 * touch than a dropdown. Grouped by quality tier: manual-maintained
 * languages up top, machine-translated below. First selection of an
 * auto-translated language triggers a one-time warning banner.
 *
 * Safe-harbor: each row shows native name + ISO code as a recognizable
 * anchor, so a user stuck in an unfamiliar language can still identify
 * their own language.
 */
export default function LanguagePicker({ currentLang, onChange }: Props) {
    const { t } = useTranslation();
    const [search, setSearch] = useState('');
    const [pendingAutoLang, setPendingAutoLang] = useState<LanguageOption | null>(null);

    const manual = languagesManifest.manual as LanguageOption[];
    const auto = languagesManifest.auto as LanguageOption[];

    const matches = (opt: LanguageOption, query: string) => {
        if (!query) return true;
        const q = query.toLowerCase().trim();
        return (
            opt.code.toLowerCase().includes(q) ||
            opt.nativeName.toLowerCase().includes(q)
        );
    };

    const filteredManual = useMemo(
        () => manual.filter(l => matches(l, search)),
        [manual, search],
    );
    const filteredAuto = useMemo(
        () => auto.filter(l => matches(l, search)),
        [auto, search],
    );

    function handlePick(opt: LanguageOption) {
        onChange(opt.code);
        if (opt.quality === 'auto' && !hasSeenBanner(opt.code)) {
            setPendingAutoLang(opt);
        } else {
            setPendingAutoLang(null);
        }
    }

    function dismissBanner() {
        if (pendingAutoLang) markBannerSeen(pendingAutoLang.code);
        setPendingAutoLang(null);
    }

    const totalMatches = filteredManual.length + filteredAuto.length;

    return (
        <div>
            <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 10 }}>
                <h4 style={{ fontWeight: 500, color: 'var(--tx)', margin: 0 }}>
                    {t('languageSelect.title', 'Interface language')}
                </h4>
                <span style={{ fontSize: 11, color: 'var(--tx3)' }}>
                    Language
                </span>
            </div>

            <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={t('languageSelect.searchPlaceholder', 'Search languages…')}
                aria-label={t('languageSelect.searchPlaceholder', 'Search languages…')}
                style={{
                    width: '100%',
                    padding: '10px 12px',
                    borderRadius: 8,
                    border: '1px solid var(--b)',
                    background: 'var(--sf2)',
                    color: 'var(--tx)',
                    fontSize: 13,
                    marginBottom: 16,
                    outline: 'none',
                }}
            />

            {totalMatches === 0 && (
                <div style={{ padding: 16, textAlign: 'center', color: 'var(--tx3)', fontSize: 13 }}>
                    {t('languageSelect.noMatches', 'No languages match your search.')}
                </div>
            )}

            {filteredManual.length > 0 && (
                <Section
                    heading={t('languageSelect.fullyTranslated', 'Fully translated')}
                    items={filteredManual}
                    currentLang={currentLang}
                    onPick={handlePick}
                    badge={(opt) => (opt.quality === 'manual' ? t('languageSelect.badgeManual', 'Official') : null)}
                />
            )}

            {filteredAuto.length > 0 && (
                <Section
                    heading={t('languageSelect.autoTranslated', 'Machine-translated (beta)')}
                    items={filteredAuto}
                    currentLang={currentLang}
                    onPick={handlePick}
                    badge={() => t('languageSelect.badgeAuto', 'β')}
                />
            )}

            {pendingAutoLang && (
                <LanguageBanner
                    langCode={pendingAutoLang.code}
                    nativeName={pendingAutoLang.nativeName}
                    onDismiss={dismissBanner}
                />
            )}

            <div style={{ marginTop: 16, fontSize: 12, color: 'var(--tx3)' }}>
                <a
                    href="https://github.com/dotradepro/SelenaCore/issues/new?title=Language+request%3A+&labels=i18n,language-request"
                    target="_blank"
                    rel="noreferrer"
                    style={{ color: 'var(--ac)', textDecoration: 'none' }}
                >
                    {t('languageSelect.requestLanguage', 'Request a new language →')}
                </a>
            </div>
        </div>
    );
}

function Section({
    heading,
    items,
    currentLang,
    onPick,
    badge,
}: {
    heading: string;
    items: LanguageOption[];
    currentLang: string;
    onPick: (opt: LanguageOption) => void;
    badge: (opt: LanguageOption) => string | null;
}) {
    return (
        <div style={{ marginBottom: 20 }}>
            <div
                style={{
                    fontSize: 11,
                    fontWeight: 500,
                    textTransform: 'uppercase',
                    letterSpacing: '.05em',
                    color: 'var(--tx3)',
                    marginBottom: 8,
                }}
            >
                {heading}
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 8 }}>
                {items.map((opt) => {
                    const isActive = opt.code === currentLang;
                    const tag = badge(opt);
                    return (
                        <button
                            key={opt.code}
                            onClick={() => onPick(opt)}
                            aria-pressed={isActive}
                            style={{
                                padding: '10px 14px',
                                borderRadius: 8,
                                border: `1px solid ${isActive ? 'var(--ac)' : 'var(--b)'}`,
                                background: isActive ? 'rgba(79,140,247,.08)' : 'var(--sf2)',
                                color: 'var(--tx)',
                                cursor: 'pointer',
                                display: 'flex',
                                alignItems: 'center',
                                gap: 10,
                                textAlign: 'left',
                                transition: 'border-color .15s, background .15s',
                            }}
                        >
                            <div style={{ flex: 1, minWidth: 0 }}>
                                <div style={{ fontSize: 14, fontWeight: 500, color: isActive ? 'var(--ac)' : 'var(--tx)' }}>
                                    {opt.nativeName}
                                </div>
                                <div style={{ fontSize: 11, color: 'var(--tx3)', marginTop: 2, textTransform: 'uppercase', letterSpacing: '.04em' }}>
                                    {opt.code}
                                </div>
                            </div>
                            {tag && (
                                <span
                                    style={{
                                        fontSize: 10,
                                        padding: '2px 6px',
                                        borderRadius: 4,
                                        background: opt.quality === 'auto'
                                            ? 'rgba(250,176,5,.15)'
                                            : 'rgba(79,140,247,.15)',
                                        color: opt.quality === 'auto' ? 'rgb(250,176,5)' : 'var(--ac)',
                                        fontWeight: 500,
                                    }}
                                >
                                    {tag}
                                </span>
                            )}
                        </button>
                    );
                })}
            </div>
        </div>
    );
}
