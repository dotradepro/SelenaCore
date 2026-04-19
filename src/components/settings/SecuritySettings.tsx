import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

interface Flags {
    edit_mode_pin: boolean;
    device_toggle_pin: boolean;
    kiosk_mode: boolean;
}

const DEFAULTS: Flags = {
    edit_mode_pin: true,
    device_toggle_pin: false,
    kiosk_mode: false,
};

/**
 * Settings → Users → Interface protection.
 *
 * Three toggles that gate destructive / noisy actions behind a PIN:
 *   • edit_mode_pin    — Dashboard "Edit" requires PIN (default on)
 *   • device_toggle_pin — every widget tap requires PIN (default off;
 *                         noisy for daily use, only for public kiosks)
 *   • kiosk_mode        — dashboard is view-only until elevated;
 *                         subsumes edit_mode_pin (default off)
 *
 * Reads + writes /api/ui/security. Save is optimistic — we PATCH the
 * full object on every flip and only display a toast if the server
 * rejects (rare; the endpoint just writes core.yaml).
 */
export default function SecuritySettings() {
    const { t } = useTranslation();
    const [flags, setFlags] = useState<Flags>(DEFAULTS);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const r = await fetch('/api/ui/security');
                if (r.ok && !cancelled) setFlags(await r.json());
            } catch {
                /* keep defaults */
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => { cancelled = true; };
    }, []);

    const save = async (next: Flags) => {
        setFlags(next);
        setError(null);
        try {
            const r = await fetch('/api/ui/security', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(next),
            });
            if (!r.ok) setError(t('security.saveFailed', 'Save failed'));
        } catch {
            setError(t('security.saveFailed', 'Save failed'));
        }
    };

    const row = (
        key: keyof Flags,
        labelKey: string,
        descKey: string,
    ) => (
        <label
            key={key}
            style={{
                display: 'flex',
                alignItems: 'flex-start',
                gap: 12,
                padding: '12px 0',
                borderTop: '1px solid var(--b)',
                cursor: 'pointer',
            }}
        >
            <input
                type="checkbox"
                checked={flags[key]}
                disabled={loading}
                onChange={(e) => save({ ...flags, [key]: e.target.checked })}
                style={{ marginTop: 3, accentColor: 'var(--ac)' }}
            />
            <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--tx)' }}>
                    {t(labelKey)}
                </div>
                <div style={{ fontSize: 12, color: 'var(--tx3)', marginTop: 2 }}>
                    {t(descKey)}
                </div>
            </div>
        </label>
    );

    return (
        <div style={{ background: 'var(--sf)', border: '1px solid var(--b)', borderRadius: 12, padding: 20 }}>
            <h4 style={{ fontWeight: 500, marginBottom: 4, color: 'var(--tx)' }}>
                {t('security.title', 'Interface protection')}
            </h4>
            <p style={{ fontSize: 12, color: 'var(--tx3)', marginBottom: 8 }}>
                {t('security.desc', 'Control which Dashboard actions require PIN confirmation.')}
            </p>

            {row(
                'edit_mode_pin',
                'security.editModePin',
                'security.editModePinDesc',
            )}
            {row(
                'device_toggle_pin',
                'security.deviceTogglePin',
                'security.deviceTogglePinDesc',
            )}
            {row(
                'kiosk_mode',
                'security.kioskMode',
                'security.kioskModeDesc',
            )}

            {error && (
                <p style={{ fontSize: 12, color: 'var(--rd)', marginTop: 10 }}>{error}</p>
            )}
        </div>
    );
}
