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
import { useState, useEffect, useRef, useCallback, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { Lock, QrCode, KeyRound, AlertCircle, Check, RefreshCw, ArrowLeft, Delete } from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { cn } from '../lib/utils';
import { useStore } from '../store/useStore';
import { useKioskInactivity } from '../hooks/useKioskInactivity';

const GATE_INACTIVITY_MS = 5 * 60 * 1000;
const GATE_EVENTS = ['click', 'keypress', 'mousemove', 'scroll'] as const;

function useGateInactivity(active: boolean) {
    const navigate = useNavigate();
    const navigateRef = useRef(navigate);
    navigateRef.current = navigate;

    useEffect(() => {
        if (!active) return;

        let timer = setTimeout(() => navigateRef.current('/', { replace: true }), GATE_INACTIVITY_MS);

        const reset = () => {
            clearTimeout(timer);
            timer = setTimeout(() => navigateRef.current('/', { replace: true }), GATE_INACTIVITY_MS);
        };

        for (const ev of GATE_EVENTS) window.addEventListener(ev, reset, { passive: true });

        return () => {
            clearTimeout(timer);
            for (const ev of GATE_EVENTS) window.removeEventListener(ev, reset);
        };
    }, [active]);
}

const UM = '/api/ui/modules/user-manager';
const QR_POLL_MS = 2000;
const QR_EXPIRE_MS = 300_000;

// ─── PIN unlock pane (iPhone-style numpad) ───────────────────────────────────
const PIN_LENGTH = 4;

function PinPane({ onSuccess, onBack }: { onSuccess: (tok: string) => void; onBack: () => void }) {
    const { t } = useTranslation();
    const [pin, setPin] = useState('');
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [shake, setShake] = useState(false);

    const submit = useCallback(async (fullPin: string) => {
        setLoading(true);
        setError(null);
        try {
            const token = (() => {
                try { return sessionStorage.getItem('selena_session') || localStorage.getItem('selena_device') || ''; } catch { return ''; }
            })();
            const res = await fetch(`${UM}/auth/pin/confirm`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...(token ? { 'X-Device-Token': token } : {}),
                },
                credentials: 'include',
                body: JSON.stringify({ pin: fullPin }),
            });
            const body = await res.json().catch(() => ({}));
            if (!res.ok) {
                setError(body.detail ?? t('auth.pinIncorrect'));
                setShake(true);
                setTimeout(() => { setShake(false); setPin(''); }, 500);
                return;
            }
            onSuccess(body.elevated_token);
        } catch {
            setError(t('common.error'));
            setShake(true);
            setTimeout(() => { setShake(false); setPin(''); }, 500);
        } finally {
            setLoading(false);
        }
    }, [onSuccess, t]);

    const addDigit = useCallback((d: string) => {
        if (loading) return;
        setError(null);
        setPin(prev => {
            const next = prev + d;
            if (next.length >= PIN_LENGTH) setTimeout(() => submit(next), 150);
            return next.length <= PIN_LENGTH ? next : prev;
        });
    }, [loading, submit]);

    const deleteDigit = useCallback(() => {
        if (loading) return;
        setError(null);
        setPin(prev => prev.slice(0, -1));
    }, [loading]);

    useEffect(() => {
        const handler = (e: globalThis.KeyboardEvent) => {
            if (loading) return;
            if (e.key >= '0' && e.key <= '9') addDigit(e.key);
            else if (e.key === 'Backspace') deleteDigit();
        };
        window.addEventListener('keydown', handler);
        return () => window.removeEventListener('keydown', handler);
    }, [addDigit, deleteDigit, loading]);

    const rows = [['1','2','3'],['4','5','6'],['7','8','9'],['back','0','del']];

    return (
        <div className="flex flex-col items-center">
            {/* PIN dots */}
            <div className={cn("flex gap-3 justify-center mb-6", shake && "animate-[shake_0.4s_ease-in-out]")}>
                {Array.from({ length: PIN_LENGTH }).map((_, i) => (
                    <div key={i} className={cn("w-3 h-3 rounded-full transition-all duration-200", i < pin.length ? "scale-110" : "")}
                        style={{ background: i < pin.length ? 'var(--ac, #7c3aed)' : 'var(--b, rgba(255,255,255,0.1))' }} />
                ))}
            </div>

            {error && (
                <div className="flex items-center gap-2 text-xs text-red-400 mb-4 px-3 py-2 rounded-xl bg-red-500/10 border border-red-500/20">
                    <AlertCircle size={13} className="shrink-0" /><span>{error}</span>
                </div>
            )}

            {loading ? (
                <div className="flex justify-center py-6">
                    <div className="w-5 h-5 border-2 border-violet-500 border-t-transparent rounded-full animate-spin" />
                </div>
            ) : (
                <div className="grid grid-cols-3 gap-3 mx-auto" style={{ width: 'fit-content' }}>
                    {rows.flat().map((key, i) => {
                        if (key === 'back') return (
                            <button key="back" onClick={onBack}
                                className="w-[64px] h-[64px] rounded-full flex items-center justify-center transition-all duration-150 active:scale-90"
                                style={{ color: 'var(--tx2)' }}><ArrowLeft size={20} /></button>
                        );
                        if (key === 'del') return (
                            <button key="del" onClick={deleteDigit}
                                className="w-[64px] h-[64px] rounded-full flex items-center justify-center transition-all duration-150 active:scale-90"
                                style={{ color: 'var(--tx2)' }}><Delete size={18} /></button>
                        );
                        return (
                            <button key={key} onClick={() => addDigit(key)}
                                className="numpad-key w-[64px] h-[64px] rounded-full text-xl font-light flex items-center justify-center transition-all duration-150 active:scale-90">{key}</button>
                        );
                    })}
                </div>
            )}
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

    // Inactivity timer — while lock screen is visible, redirect to / after 5 min
    useGateInactivity(!elevatedToken);
    // Inactivity timer — while elevated, revoke session after 5 min of no interaction
    useKioskInactivity();

    if (elevatedToken) {
        return <>{children}</>;
    }

    const handleSuccess = (tok: string) => {
        setElevatedToken(tok);
        try { sessionStorage.setItem('selena_elevated', tok); } catch { /* ignore */ }
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
                                        ? <PinPane onSuccess={handleSuccess} onBack={() => window.history.back()} />
                                        : <QrPane onSuccess={handleSuccess} />
                                    }
                                </motion.div>
                            </AnimatePresence>
                        </div>
                    </div>
                </div>
            </motion.div>
        </AnimatePresence>
    );
}
