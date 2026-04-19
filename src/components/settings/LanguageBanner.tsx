import { useTranslation } from 'react-i18next';

interface Props {
    langCode: string;
    nativeName: string;
    onDismiss: () => void;
}

/**
 * First-time warning shown after a user picks an auto-translated language.
 * Rendered inline by LanguagePicker; the parent is responsible for
 * deciding visibility (tracked in localStorage per lang code).
 *
 * Intentionally non-blocking — user can dismiss and keep the language.
 */
export default function LanguageBanner({ langCode, nativeName, onDismiss }: Props) {
    const { t } = useTranslation();
    return (
        <div
            role="status"
            style={{
                marginTop: 12,
                padding: 12,
                borderRadius: 10,
                background: 'rgba(250, 176, 5, 0.06)',
                border: '1px solid rgba(250, 176, 5, 0.22)',
                color: 'var(--tx)',
                fontSize: 13,
                lineHeight: 1.45,
            }}
        >
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
                <span aria-hidden style={{ fontSize: 18, lineHeight: 1, flexShrink: 0 }}>⚠</span>
                <div style={{ flex: 1 }}>
                    <div style={{ fontWeight: 500, marginBottom: 4 }}>
                        {t('languageSelect.autoWarningTitle', 'Machine-translated interface')}
                    </div>
                    <div style={{ color: 'var(--tx2)', fontSize: 12 }}>
                        {t('languageSelect.autoWarningBody', {
                            defaultValue: '{{lang}} is translated automatically via Argos Translate. Parts of the UI may read oddly — help us improve by submitting a PR on GitHub.',
                            lang: nativeName,
                        })}
                    </div>
                    <div style={{ marginTop: 10, display: 'flex', gap: 8 }}>
                        <a
                            href={`https://github.com/dotradepro/SelenaCore/issues/new?title=Translation+improvement%3A+${encodeURIComponent(langCode)}&labels=i18n,translation-improvement`}
                            target="_blank"
                            rel="noreferrer"
                            style={{
                                fontSize: 12,
                                color: 'var(--ac)',
                                textDecoration: 'none',
                                fontWeight: 500,
                            }}
                        >
                            {t('languageSelect.helpImprove', 'Help improve →')}
                        </a>
                        <button
                            onClick={onDismiss}
                            style={{
                                marginLeft: 'auto',
                                padding: '4px 12px',
                                borderRadius: 6,
                                border: '1px solid var(--b)',
                                background: 'var(--sf2)',
                                color: 'var(--tx)',
                                fontSize: 12,
                                cursor: 'pointer',
                            }}
                        >
                            {t('languageSelect.dismiss', 'Got it')}
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );
}
