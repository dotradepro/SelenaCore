/**
 * AuthWall — full-screen authorization gate for unregistered browsers.
 *
 * Shown when the wizard is complete but this browser has no valid device_token.
 * The user must log in with username + PIN to proceed.
 * After successful registration the device_token is stored and initAuth() is called.
 */
import { useState, type KeyboardEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { Eye, EyeOff, AlertCircle, Smartphone, Check } from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { cn } from '../lib/utils';
import { useStore } from '../store/useStore';

const UM = '/api/ui/modules/user-manager';

export default function AuthWall() {
    const { t } = useTranslation();
    const initAuth = useStore((s) => s.initAuth);

    const [username, setUsername] = useState('');
    const [pin, setPin] = useState('');
    const [deviceName, setDeviceName] = useState('');
    const [showPin, setShowPin] = useState(false);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [done, setDone] = useState(false);

    const canSubmit = username.trim().length > 0 && pin.length >= 4 && !loading;

    const submit = async () => {
        if (!canSubmit) return;
        setLoading(true);
        setError(null);
        try {
            const name = deviceName.trim() || navigator.userAgent.slice(0, 40);
            const res = await fetch(`${UM}/auth/device/register`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({ username: username.trim(), pin, device_name: name }),
            });
            const body = await res.json().catch(() => ({}));
            if (!res.ok) {
                setError(body.detail ?? t('auth.registerFailed'));
                setPin('');
                return;
            }
            // Persist token immediately
            if (body.device_token) {
                try {
                    localStorage.setItem('selena_device', body.device_token);
                    document.cookie = `selena_device=${body.device_token}; max-age=${60 * 60 * 24 * 30}; path=/; samesite=strict`;
                } catch { /* ignore */ }
            }
            setDone(true);
            setTimeout(() => initAuth(), 800);
        } catch {
            setError(t('common.error'));
        } finally {
            setLoading(false);
        }
    };

    const handleKey = (e: KeyboardEvent) => {
        if (e.key === 'Enter') submit();
    };

    return (
        <div
            className="fixed inset-0 z-50 flex items-center justify-center"
            style={{ background: 'var(--bg)' }}
        >
            {/* Background pattern */}
            <div className="absolute inset-0 opacity-[0.03] pointer-events-none"
                style={{
                    backgroundImage: 'radial-gradient(circle at 1px 1px, var(--tx) 1px, transparent 0)',
                    backgroundSize: '28px 28px',
                }}
            />

            {/* Glow */}
            <div className="absolute w-96 h-96 rounded-full bg-violet-600/10 blur-3xl pointer-events-none" />

            <AnimatePresence mode="wait">
                {done ? (
                    <motion.div
                        key="done"
                        initial={{ opacity: 0, scale: 0.9 }}
                        animate={{ opacity: 1, scale: 1 }}
                        className="flex flex-col items-center gap-4"
                    >
                        <div className="w-16 h-16 rounded-full bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center">
                            <Check size={28} className="text-emerald-400" />
                        </div>
                        <p className="text-base font-semibold" style={{ color: 'var(--tx)' }}>
                            {t('auth.registerSuccess')}
                        </p>
                    </motion.div>
                ) : (
                    <motion.div
                        key="form"
                        initial={{ opacity: 0, y: 16 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: -8 }}
                        className="relative z-10 w-full max-w-sm mx-4"
                    >
                        {/* Card */}
                        <div
                            className="rounded-2xl border p-8 shadow-2xl"
                            style={{ background: 'var(--sf)', borderColor: 'var(--b)' }}
                        >
                            {/* Logo + title */}
                            <div className="flex flex-col items-center gap-3 mb-8">
                                <div style={{
                                    width: 48, height: 48,
                                    background: 'var(--ac)',
                                    borderRadius: 14,
                                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                                }}>
                                    <svg viewBox="0 0 14 14" fill="none" width="22" height="22">
                                        <path d="M7 1L2 4v5.5L7 13l5-3.5V4L7 1Z" stroke="white" strokeWidth="1.3" strokeLinejoin="round" />
                                        <circle cx="7" cy="7" r="1.8" fill="white" />
                                    </svg>
                                </div>
                                <div className="text-center">
                                    <p className="text-lg font-bold" style={{ color: 'var(--tx)' }}>
                                        SmartHome
                                    </p>
                                    <p className="text-sm" style={{ color: 'var(--tx3)' }}>
                                        {t('auth.wallSubtitle')}
                                    </p>
                                </div>
                            </div>

                            {/* Device icon */}
                            <div className="flex items-center gap-2 mb-6 p-3 rounded-xl border"
                                style={{ borderColor: 'var(--b2)', background: 'var(--sf3)' }}
                            >
                                <Smartphone size={14} className="text-violet-400 shrink-0" />
                                <span className="text-xs" style={{ color: 'var(--tx3)' }}>
                                    {t('auth.wallNotice')}
                                </span>
                            </div>

                            {/* Form */}
                            <div className="space-y-4">
                                {/* Username */}
                                <div>
                                    <label className="block text-xs font-medium mb-1.5" style={{ color: 'var(--tx2)' }}>
                                        {t('auth.username')}
                                    </label>
                                    <input
                                        type="text"
                                        value={username}
                                        onChange={(e) => { setUsername(e.target.value); setError(null); }}
                                        onKeyDown={handleKey}
                                        disabled={loading}
                                        autoComplete="username"
                                        className="w-full rounded-xl px-3 py-2.5 text-sm focus:outline-none transition-all"
                                        style={{
                                            background: 'var(--bg)',
                                            border: `1px solid ${error ? '#ef4444' : 'var(--b)'}`,
                                            color: 'var(--tx)',
                                        }}
                                        placeholder={t('auth.usernamePlaceholder')}
                                    />
                                </div>

                                {/* PIN */}
                                <div>
                                    <label className="block text-xs font-medium mb-1.5" style={{ color: 'var(--tx2)' }}>
                                        {t('auth.pin')}
                                    </label>
                                    <div className="relative">
                                        <input
                                            type={showPin ? 'text' : 'password'}
                                            inputMode="numeric"
                                            pattern="[0-9]*"
                                            maxLength={8}
                                            value={pin}
                                            onChange={(e) => { setPin(e.target.value.replace(/\D/g, '')); setError(null); }}
                                            onKeyDown={handleKey}
                                            disabled={loading}
                                            autoComplete="current-password"
                                            className="w-full rounded-xl px-3 py-2.5 pr-10 text-sm font-mono tracking-widest focus:outline-none transition-all"
                                            style={{
                                                background: 'var(--bg)',
                                                border: `1px solid ${error ? '#ef4444' : 'var(--b)'}`,
                                                color: 'var(--tx)',
                                            }}
                                            placeholder="••••"
                                        />
                                        <button
                                            type="button"
                                            onClick={() => setShowPin((p) => !p)}
                                            className="absolute right-3 top-1/2 -translate-y-1/2"
                                            style={{ color: 'var(--tx3)' }}
                                        >
                                            {showPin ? <EyeOff size={14} /> : <Eye size={14} />}
                                        </button>
                                    </div>
                                </div>

                                {/* Device name (optional, collapsible) */}
                                <div>
                                    <label className="block text-xs font-medium mb-1.5" style={{ color: 'var(--tx2)' }}>
                                        {t('auth.deviceName')}
                                        <span className="ml-1 font-normal opacity-60">({t('auth.optional')})</span>
                                    </label>
                                    <input
                                        type="text"
                                        value={deviceName}
                                        onChange={(e) => setDeviceName(e.target.value)}
                                        disabled={loading}
                                        className="w-full rounded-xl px-3 py-2.5 text-sm focus:outline-none transition-all"
                                        style={{
                                            background: 'var(--bg)',
                                            border: '1px solid var(--b)',
                                            color: 'var(--tx)',
                                        }}
                                        placeholder={t('auth.deviceNamePlaceholder')}
                                    />
                                </div>

                                {/* Error */}
                                {error && (
                                    <div className="flex items-center gap-2 text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-xl px-3 py-2.5">
                                        <AlertCircle size={13} className="shrink-0" />
                                        <span>{error}</span>
                                    </div>
                                )}

                                {/* Submit */}
                                <button
                                    onClick={submit}
                                    disabled={!canSubmit}
                                    className={cn(
                                        'w-full py-3 rounded-xl text-sm font-semibold text-white transition-all',
                                        'bg-violet-600 hover:bg-violet-500 active:scale-[0.98]',
                                        'disabled:opacity-50 disabled:cursor-not-allowed',
                                        'flex items-center justify-center gap-2',
                                    )}
                                >
                                    {loading ? (
                                        <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                                    ) : (
                                        t('auth.wallEnter')
                                    )}
                                </button>
                            </div>
                        </div>

                        {/* Footer hint */}
                        <p className="text-center text-xs mt-4" style={{ color: 'var(--tx3)' }}>
                            {t('auth.wallFooter')}
                        </p>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    );
}
