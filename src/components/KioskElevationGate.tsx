/**
 * KioskElevationGate — wraps restricted sections on the kiosk (device) screen.
 *
 * On a kiosk (localhost / 127.0.0.1 / ?kiosk=1) the dashboard is always
 * visible. Settings, System, Integrity and Modules sections are "closed" —
 * navigating to them shows this lock overlay instead of the content.
 *
 * Two ways to unlock:
 *  • PIN  — user enters their PIN (POST /auth/pin/confirm)
 *  • QR   — kiosk shows a QR code; a registered phone scans + approves
 *           (POST /auth/qr/approve/{session_id}) → kiosk receives elevated_token
 *
 * After unlock the elevated session lasts 10 minutes server-side.
 */
import { useState, useEffect, useRef, type ReactNode, type KeyboardEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { Lock, Eye, EyeOff, QrCode, KeyRound, AlertCircle, Check, RefreshCw, X } from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { cn } from '../lib/utils';
import { useStore } from '../store/useStore';

const UM = '/api/ui/modules/user-manager';
const QR_POLL_MS = 2000;
const QR_EXPIRE_MS = 300_000;

// ─── PIN unlock pane ──────────────────────────────────────────────────────────
function PinPane({ onSuccess }: { onSuccess: (tok: string) => void }) {
    const { t } = useTranslation();
    const [pin, setPin] = useState('');
    const [show, setShow] = useState(false);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const submit = async () => {
        if (pin.length < 4 || loading) return;
        setLoading(true);
        setError(null);
        try {
            const token = (() => {
                try { return localStorage.getItem('selena_device') || ''; } catch { return ''; }
            })();
            const res = await fetch(`${UM}/auth/pin/confirm`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Device-Token': token,
                },
                credentials: 'include',
                body: JSON.stringify({ pin }),
            });
            const body = await res.json().catch(() => ({}));
            if (!res.ok) {
                setError(body.detail ?? t('auth.pinIncorrect'));
                setPin('');
                return;
            }
            onSuccess(body.elevated_token);
        } catch {
            setError(t('common.error'));
        } finally {
            setLoading(false);
        }
    };

    const handleKey = (e: KeyboardEvent) => { if (e.key === 'Enter') submit(); };

    return (
        <div className="space-y-4">
            <div>
                <label className="block text-xs font-medium mb-1.5" style={{ color: 'var(--tx2)' }}>
                    {t('auth.pin')}
                </label>
                <div className="relative">
                    <input
                        type={show ? 'text' : 'password'}
                        inputMode="numeric"
                        pattern="[0-9]*"
                        maxLength={8}
                        value={pin}
                        onChange={(e) => { setPin(e.target.value.replace(/\D/g, '')); setError(null); }}
                        onKeyDown={handleKey}
                        disabled={loading}
                        autoFocus
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
                        onClick={() => setShow(s => !s)}
                        className="absolute right-3 top-1/2 -translate-y-1/2"
                        style={{ color: 'var(--tx3)' }}
                    >
                        {show ? <EyeOff size={14} /> : <Eye size={14} />}
                    </button>
                </div>
            </div>

            {error && (
                <div className="flex items-center gap-2 text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-xl px-3 py-2.5">
                    <AlertCircle size={13} className="shrink-0" />
                    <span>{error}</span>
                </div>
            )}

            <button
                onClick={submit}
                disabled={pin.length < 4 || loading}
                className={cn(
                    'w-full py-2.5 rounded-xl text-sm font-semibold text-white transition-all',
                    'bg-violet-600 hover:bg-violet-500 active:scale-[0.98]',
                    'disabled:opacity-50 disabled:cursor-not-allowed',
                    'flex items-center justify-center gap-2',
                )}
            >
                {loading
                    ? <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                    : t('kiosk.unlock')}
            </button>
        </div>
    );
}

// ─── QR unlock pane ───────────────────────────────────────────────────────────
function QrPane({ onSuccess }: { onSuccess: (tok: string) => void }) {
    const { t } = useTranslation();
    const [qrImage, setQrImage] = useState<string | null>(null);
    const [joinUrl, setJoinUrl] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [expired, setExpired] = useState(false);
    const [approved, setApproved] = useState(false);
    const [countdown, setCountdown] = useState<number>(0); // seconds remaining

    const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
    const expireRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

    const stop = () => {
        if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
        if (expireRef.current) { clearTimeout(expireRef.current); expireRef.current = null; }
        if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
    };

    const start = async () => {
        stop();
        setLoading(true); setError(null); setExpired(false); setApproved(false); setQrImage(null);
        try {
            const res = await fetch(`${UM}/auth/qr/start`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ mode: 'elevate' }),
            });
            if (!res.ok) throw new Error('Failed to create QR session');
            const data = await res.json();
            setQrImage(data.qr_image ?? null);
            setJoinUrl(data.join_url ?? null);
            const ttl: number = data.expires_in ?? 300;
            setCountdown(ttl);
            setLoading(false);

            // Countdown ticker
            timerRef.current = setInterval(() => {
                setCountdown(prev => {
                    if (prev <= 1) { clearInterval(timerRef.current!); timerRef.current = null; return 0; }
                    return prev - 1;
                });
            }, 1000);

            pollRef.current = setInterval(async () => {
                try {
                    const st = await fetch(`${UM}/auth/qr/status/${data.session_id}`);
                    if (st.status === 410) { stop(); setExpired(true); return; }
                    if (!st.ok) return;
                    const s = await st.json();
                    if (s.status === 'complete' && s.elevated_token) {
                        stop(); setApproved(true);
                        setTimeout(() => onSuccess(s.elevated_token), 600);
                    }
                } catch { /* ignore */ }
            }, QR_POLL_MS);

            expireRef.current = setTimeout(() => { stop(); setExpired(true); }, QR_EXPIRE_MS);
        } catch {
            setLoading(false);
            setError(t('common.error'));
        }
    };

    useEffect(() => { start(); return stop; }, []); // eslint-disable-line react-hooks/exhaustive-deps

    if (approved) {
        return (
            <div className="flex flex-col items-center gap-3 py-6">
                <div className="w-12 h-12 rounded-full bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center">
                    <Check size={22} className="text-emerald-400" />
                </div>
                <p className="text-sm font-medium" style={{ color: 'var(--tx)' }}>{t('kiosk.approved')}</p>
            </div>
        );
    }

    if (expired) {
        return (
            <div className="flex flex-col items-center gap-3 py-4">
                <p className="text-sm text-center" style={{ color: 'var(--tx2)' }}>{t('auth.qrExpired')}</p>
                <button onClick={start} className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium bg-violet-600 text-white">
                    <RefreshCw size={12} /> {t('auth.qrRefresh')}
                </button>
            </div>
        );
    }

    if (loading) {
        return <div className="flex justify-center py-8">
            <div className="w-7 h-7 border-2 border-violet-500 border-t-transparent rounded-full animate-spin" />
        </div>;
    }

    return (
        <div className="flex flex-col items-center gap-3">
            {qrImage ? (
                <div className="p-2 rounded-xl bg-white">
                    <img src={qrImage} alt="QR" className="w-40 h-40" />
                </div>
            ) : (
                <div className="w-40 h-40 rounded-xl border-2 border-dashed flex items-center justify-center"
                    style={{ borderColor: 'var(--b)' }}>
                    <QrCode size={36} style={{ color: 'var(--tx3)' }} />
                </div>
            )}
            {/* Countdown timer */}
            {countdown > 0 && (
                <div className={cn(
                    'flex items-center gap-1 text-xs font-mono tabular-nums',
                    countdown < 60 ? 'text-red-400' : '',
                )} style={countdown >= 60 ? { color: 'var(--tx3)' } : {}}>
                    {t('auth.qrTimer')}&nbsp;
                    {String(Math.floor(countdown / 60)).padStart(2, '0')}:{String(countdown % 60).padStart(2, '0')}
                </div>
            )}
            <p className="text-xs text-center" style={{ color: 'var(--tx2)' }}>{t('kiosk.qrDesc')}</p>
            <div className="flex items-center gap-1.5 text-xs" style={{ color: 'var(--tx3)' }}>
                <span className="w-1.5 h-1.5 rounded-full bg-violet-400 animate-pulse" />
                {t('auth.qrWaiting')}
            </div>
            {joinUrl && (
                <p className="text-xs text-center break-all" style={{ color: 'var(--tx3)' }}>
                    <a href={joinUrl} target="_blank" rel="noreferrer" className="text-violet-400 hover:underline">
                        {t('auth.qrOpenLink')}
                    </a>
                </p>
            )}
            {error && <p className="text-xs text-red-400">{error}</p>}
        </div>
    );
}

// ─── Gate ─────────────────────────────────────────────────────────────────────
type Tab = 'pin' | 'qr';

export default function KioskElevationGate({ children }: { children: ReactNode }) {
    const { t } = useTranslation();
    const elevatedToken = useStore(s => s.elevatedToken);
    const setElevatedToken = useStore(s => s.setElevatedToken);
    const [tab, setTab] = useState<Tab>('pin');
    const [unlocked, setUnlocked] = useState(false);

    // Re-lock when elevated token disappears (expired or cleared)
    useEffect(() => {
        if (!elevatedToken) setUnlocked(false);
    }, [elevatedToken]);

    if (unlocked && elevatedToken) {
        return <>{children}</>;
    }

    const handleSuccess = (tok: string) => {
        setElevatedToken(tok);
        try { sessionStorage.setItem('selena_elevated', tok); } catch { /* ignore */ }
        setUnlocked(true);
    };

    return (
        <AnimatePresence mode="wait">
            <motion.div
                key="lock"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="fixed inset-0 z-40 flex items-center justify-center"
                style={{ background: 'var(--bg)' }}
            >
                {/* Subtle grid */}
                <div className="absolute inset-0 opacity-[0.025] pointer-events-none"
                    style={{
                        backgroundImage: 'radial-gradient(circle at 1px 1px, var(--tx) 1px, transparent 0)',
                        backgroundSize: '28px 28px',
                    }}
                />
                <div className="absolute w-80 h-80 rounded-full bg-violet-600/8 blur-3xl pointer-events-none" />

                <div className="relative z-10 w-full max-w-xs mx-4">
                    <div className="rounded-2xl border shadow-2xl overflow-hidden"
                        style={{ background: 'var(--sf)', borderColor: 'var(--b)' }}
                    >
                        {/* Header */}
                        <div className="px-6 pt-6 pb-4">
                            <div className="flex items-center justify-between mb-4">
                                <div className="flex items-center gap-2">
                                    <div className="w-8 h-8 rounded-lg bg-violet-600/10 border border-violet-500/20 flex items-center justify-center">
                                        <Lock size={14} className="text-violet-400" />
                                    </div>
                                    <div>
                                        <p className="text-sm font-semibold" style={{ color: 'var(--tx)' }}>
                                            {t('kiosk.restrictedTitle')}
                                        </p>
                                        <p className="text-xs" style={{ color: 'var(--tx3)' }}>
                                            {t('kiosk.restrictedDesc')}
                                        </p>
                                    </div>
                                </div>
                            </div>

                            {/* Tabs */}
                            <div className="flex rounded-xl p-1" style={{ background: 'var(--bg)' }}>
                                {([['pin', KeyRound, 'auth.tabPin'], ['qr', QrCode, 'auth.tabQr']] as const).map(
                                    ([id, Icon, key]) => (
                                        <button key={id} onClick={() => setTab(id)}
                                            className={cn(
                                                'flex-1 flex items-center justify-center gap-1.5 py-1.5 rounded-lg text-xs font-medium transition-all',
                                                tab === id ? 'bg-violet-600 text-white shadow' : 'hover:opacity-80',
                                            )}
                                            style={tab !== id ? { color: 'var(--tx2)' } : {}}
                                        >
                                            <Icon size={12} />
                                            {t(key)}
                                        </button>
                                    )
                                )}
                            </div>
                        </div>

                        {/* Tab content */}
                        <div className="px-6 pb-6">
                            <AnimatePresence mode="wait">
                                <motion.div
                                    key={tab}
                                    initial={{ opacity: 0, x: tab === 'pin' ? -8 : 8 }}
                                    animate={{ opacity: 1, x: 0 }}
                                    exit={{ opacity: 0 }}
                                    transition={{ duration: 0.12 }}
                                >
                                    {tab === 'pin'
                                        ? <PinPane onSuccess={handleSuccess} />
                                        : <QrPane onSuccess={handleSuccess} />
                                    }
                                </motion.div>
                            </AnimatePresence>
                        </div>
                    </div>

                    {/* "Go back" hint */}
                    <button
                        onClick={() => window.history.back()}
                        className="flex items-center gap-1.5 mx-auto mt-3 text-xs hover:opacity-80 transition-opacity"
                        style={{ color: 'var(--tx3)' }}
                    >
                        <X size={11} />
                        {t('common.back')}
                    </button>
                </div>
            </motion.div>
        </AnimatePresence>
    );
}
