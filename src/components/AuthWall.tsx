/**
 * AuthWall — full-screen authorization gate for unregistered browsers.
 *
 * Two tabs:
 *  • PIN  — enter username + PIN to authenticate. Creates a device_token.
 *  • QR   — this browser shows a QR code; a registered device scans and approves.
 *           Polls /auth/qr/status until complete, then stores device_token.
 *
 * Kiosk devices (localhost / 127.0.0.1 / ?kiosk=1) bypass this wall entirely
 * via AuthGate in App.tsx.
 */
import { useState, useEffect, useRef, type KeyboardEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { Eye, EyeOff, AlertCircle, Smartphone, QrCode, KeyRound, Check, RefreshCw } from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { cn } from '../lib/utils';
import { useStore } from '../store/useStore';

const UM = '/api/ui/modules/user-manager';

// ─── PIN Tab ──────────────────────────────────────────────────────────────────
function PinTab({ onSuccess }: { onSuccess: () => void }) {
    const { t } = useTranslation();

    const [username, setUsername] = useState('');
    const [pin, setPin] = useState('');
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
            const name = navigator.userAgent.slice(0, 40);
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
            if (body.device_token) {
                try {
                    localStorage.setItem('selena_device', body.device_token);
                    document.cookie = `selena_device=${body.device_token}; max-age=${60 * 60 * 24 * 30}; path=/; samesite=strict`;
                } catch { /* ignore */ }
            }
            setDone(true);
            setTimeout(onSuccess, 800);
        } catch {
            setError(t('common.error'));
        } finally {
            setLoading(false);
        }
    };

    const handleKey = (e: KeyboardEvent) => { if (e.key === 'Enter') submit(); };

    if (done) {
        return (
            <div className="flex flex-col items-center gap-3 py-8">
                <div className="w-14 h-14 rounded-full bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center">
                    <Check size={26} className="text-emerald-400" />
                </div>
                <p className="text-sm font-semibold" style={{ color: 'var(--tx)' }}>
                    {t('auth.registerSuccess')}
                </p>
            </div>
        );
    }

    return (
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
    );
}

// ─── QR Tab ───────────────────────────────────────────────────────────────────
const QR_POLL_MS = 2000;
const QR_EXPIRE_MS = 300_000; // 5 min (backend TTL)

function QrTab({ onSuccess }: { onSuccess: () => void }) {
    const { t } = useTranslation();

    const [qrImage, setQrImage] = useState<string | null>(null);
    const [joinUrl, setJoinUrl] = useState<string | null>(null);
    const [sessionId, setSessionId] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [expired, setExpired] = useState(false);
    const [approved, setApproved] = useState(false);

    const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
    const expireRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    const stopPolling = () => {
        if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
        if (expireRef.current) { clearTimeout(expireRef.current); expireRef.current = null; }
    };

    const startSession = async () => {
        stopPolling();
        setLoading(true);
        setError(null);
        setExpired(false);
        setApproved(false);
        setQrImage(null);
        setSessionId(null);
        try {
            const res = await fetch(`${UM}/auth/qr/start`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ mode: 'access' }),
            });
            if (!res.ok) throw new Error('Failed to create QR session');
            const data = await res.json();
            setQrImage(data.qr_image ?? null);
            setJoinUrl(data.join_url ?? null);
            setSessionId(data.session_id);
            setLoading(false);

            // Poll for approval
            pollRef.current = setInterval(async () => {
                try {
                    const status = await fetch(`${UM}/auth/qr/status/${data.session_id}`);
                    if (status.status === 410) {
                        // expired
                        stopPolling();
                        setExpired(true);
                        return;
                    }
                    if (!status.ok) return;
                    const s = await status.json();
                    if (s.status === 'complete' && s.device_token) {
                        stopPolling();
                        try {
                            localStorage.setItem('selena_device', s.device_token);
                            document.cookie = `selena_device=${s.device_token}; max-age=${60 * 60 * 24 * 30}; path=/; samesite=strict`;
                        } catch { /* ignore */ }
                        setApproved(true);
                        setTimeout(onSuccess, 800);
                    }
                } catch { /* network hiccup — ignore */ }
            }, QR_POLL_MS);

            // Auto-expire at client side
            expireRef.current = setTimeout(() => {
                stopPolling();
                setExpired(true);
            }, QR_EXPIRE_MS);
        } catch {
            setLoading(false);
            setError(t('common.error'));
        }
    };

    useEffect(() => {
        startSession();
        return stopPolling;
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    if (approved) {
        return (
            <div className="flex flex-col items-center gap-3 py-8">
                <div className="w-14 h-14 rounded-full bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center">
                    <Check size={26} className="text-emerald-400" />
                </div>
                <p className="text-sm font-semibold" style={{ color: 'var(--tx)' }}>
                    {t('auth.registerSuccess')}
                </p>
            </div>
        );
    }

    if (expired) {
        return (
            <div className="flex flex-col items-center gap-4 py-6">
                <p className="text-sm text-center" style={{ color: 'var(--tx2)' }}>
                    {t('auth.qrExpired')}
                </p>
                <button
                    onClick={startSession}
                    className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium bg-violet-600 hover:bg-violet-500 text-white transition-all"
                >
                    <RefreshCw size={14} />
                    {t('auth.qrRefresh')}
                </button>
            </div>
        );
    }

    if (loading) {
        return (
            <div className="flex items-center justify-center py-12">
                <div className="w-8 h-8 border-2 border-violet-500 border-t-transparent rounded-full animate-spin" />
            </div>
        );
    }

    if (error) {
        return (
            <div className="flex flex-col items-center gap-3 py-6">
                <div className="flex items-center gap-2 text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-xl px-3 py-2.5">
                    <AlertCircle size={13} className="shrink-0" />
                    <span>{error}</span>
                </div>
                <button onClick={startSession} className="text-xs text-violet-400 hover:underline">
                    {t('auth.qrRefresh')}
                </button>
            </div>
        );
    }

    return (
        <div className="flex flex-col items-center gap-4">
            {/* QR image or fallback URL */}
            {qrImage ? (
                <div className="p-2 rounded-xl bg-white">
                    <img src={qrImage} alt="QR code" className="w-48 h-48" />
                </div>
            ) : (
                <div className="w-48 h-48 rounded-xl border-2 border-dashed flex items-center justify-center"
                    style={{ borderColor: 'var(--b)' }}>
                    <QrCode size={40} style={{ color: 'var(--tx3)' }} />
                </div>
            )}

            {/* Instructions */}
            <div className="text-center space-y-1">
                <p className="text-sm font-medium" style={{ color: 'var(--tx)' }}>
                    {t('auth.qrScanTitle')}
                </p>
                <p className="text-xs" style={{ color: 'var(--tx3)' }}>
                    {t('auth.qrScanDesc')}
                </p>
            </div>

            {/* Pulse indicator — waiting */}
            <div className="flex items-center gap-2 text-xs" style={{ color: 'var(--tx3)' }}>
                <span className="w-2 h-2 rounded-full bg-violet-400 animate-pulse" />
                {t('auth.qrWaiting')}
            </div>

            {/* Manual link fallback */}
            {joinUrl && (
                <p className="text-xs text-center break-all" style={{ color: 'var(--tx3)' }}>
                    {t('auth.qrOrOpen')}{' '}
                    <a href={joinUrl} target="_blank" rel="noreferrer"
                        className="text-violet-400 hover:underline">
                        {t('auth.qrOpenLink')}
                    </a>
                </p>
            )}
        </div>
    );
}

// ─── AuthWall shell ───────────────────────────────────────────────────────────
type Tab = 'pin' | 'qr';

export default function AuthWall() {
    const { t } = useTranslation();
    const initAuth = useStore((s) => s.initAuth);
    const [tab, setTab] = useState<Tab>('pin');
    type DoneState = false | true;
    const [globalDone, setGlobalDone] = useState<DoneState>(false);

    const handleSuccess = () => {
        setGlobalDone(true);
        setTimeout(() => initAuth(), 800);
    };

    return (
        <div
            className="fixed inset-0 z-50 flex items-center justify-center"
            style={{ background: 'var(--bg)' }}
        >
            {/* Background grid */}
            <div className="absolute inset-0 opacity-[0.03] pointer-events-none"
                style={{
                    backgroundImage: 'radial-gradient(circle at 1px 1px, var(--tx) 1px, transparent 0)',
                    backgroundSize: '28px 28px',
                }}
            />
            {/* Glow */}
            <div className="absolute w-96 h-96 rounded-full bg-violet-600/10 blur-3xl pointer-events-none" />

            <AnimatePresence mode="wait">
                {globalDone ? (
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
                        key="card"
                        initial={{ opacity: 0, y: 16 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: -8 }}
                        className="relative z-10 w-full max-w-sm mx-4"
                    >
                        <div
                            className="rounded-2xl border shadow-2xl overflow-hidden"
                            style={{ background: 'var(--sf)', borderColor: 'var(--b)' }}
                        >
                            {/* Header */}
                            <div className="px-8 pt-8 pb-6">
                                <div className="flex flex-col items-center gap-3 mb-6">
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

                                {/* Notice banner */}
                                <div className="flex items-center gap-2 p-3 rounded-xl border mb-6"
                                    style={{ borderColor: 'var(--b2)', background: 'var(--sf3)' }}
                                >
                                    <Smartphone size={14} className="text-violet-400 shrink-0" />
                                    <span className="text-xs" style={{ color: 'var(--tx3)' }}>
                                        {t('auth.wallNotice')}
                                    </span>
                                </div>

                                {/* Tab switcher */}
                                <div className="flex rounded-xl p-1 mb-6"
                                    style={{ background: 'var(--bg)' }}
                                >
                                    {([['pin', KeyRound, 'auth.tabPin'], ['qr', QrCode, 'auth.tabQr']] as const).map(
                                        ([id, Icon, key]) => (
                                            <button
                                                key={id}
                                                onClick={() => setTab(id)}
                                                className={cn(
                                                    'flex-1 flex items-center justify-center gap-1.5 py-2 rounded-lg text-xs font-medium transition-all',
                                                    tab === id
                                                        ? 'bg-violet-600 text-white shadow'
                                                        : 'hover:opacity-80',
                                                )}
                                                style={tab !== id ? { color: 'var(--tx2)' } : {}}
                                            >
                                                <Icon size={13} />
                                                {t(key)}
                                            </button>
                                        )
                                    )}
                                </div>
                            </div>

                            {/* Tab content */}
                            <div className="px-8 pb-8">
                                <AnimatePresence mode="wait">
                                    <motion.div
                                        key={tab}
                                        initial={{ opacity: 0, x: tab === 'pin' ? -12 : 12 }}
                                        animate={{ opacity: 1, x: 0 }}
                                        exit={{ opacity: 0 }}
                                        transition={{ duration: 0.15 }}
                                    >
                                        {tab === 'pin'
                                            ? <PinTab onSuccess={handleSuccess} />
                                            : <QrTab onSuccess={handleSuccess} />
                                        }
                                    </motion.div>
                                </AnimatePresence>
                            </div>
                        </div>

                        {/* Footer */}
                        <p className="text-center text-xs mt-4" style={{ color: 'var(--tx3)' }}>
                            {t('auth.wallFooter')}
                        </p>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    );
}
