/**
 * AuthWall — full-screen PIN authorization gate.
 *
 * iPhone-style numeric keypad — PIN only, no username.
 * Also supports QR tab for phone-based approval.
 *
 * Kiosk devices bypass this wall via AuthGate in App.tsx.
 */
import { useState, useEffect, useRef, useCallback, type KeyboardEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertCircle, QrCode, KeyRound, Check, RefreshCw, UserPlus, Delete, ArrowLeft } from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { cn } from '../lib/utils';
import { useStore } from '../store/useStore';

const UM = '/api/ui/modules/user-manager';
const PIN_LENGTH = 4;

// ─── Numeric Keypad (iPhone-style) ──────────────────────────────────────────

function NumPad({ onDigit, onDelete }: { onDigit: (d: string) => void; onDelete: () => void }) {
    const keys = [
        ['1', '2', '3'],
        ['4', '5', '6'],
        ['7', '8', '9'],
        ['', '0', 'del'],
    ];

    return (
        <div className="grid grid-cols-3 gap-4 mx-auto" style={{ width: 'fit-content' }}>
            {keys.flat().map((key, i) => {
                if (key === '') return <div key={i} className="w-[72px] h-[72px]" />;
                if (key === 'del') {
                    return (
                        <button
                            key="del"
                            onClick={onDelete}
                            className="w-[72px] h-[72px] rounded-full flex items-center justify-center transition-all duration-150 active:scale-90"
                            style={{ color: 'var(--tx2)' }}
                        >
                            <Delete size={22} />
                        </button>
                    );
                }
                return (
                    <button
                        key={key}
                        onClick={() => onDigit(key)}
                        className="numpad-key w-[72px] h-[72px] rounded-full text-[26px] font-light flex items-center justify-center transition-all duration-150 active:scale-90"
                    >
                        {key}
                    </button>
                );
            })}
        </div>
    );
}

// ─── PIN Dots ────────────────────────────────────────────────────────────────

function PinDots({ length, filled, shake }: { length: number; filled: number; shake: boolean }) {
    return (
        <div className={cn("flex gap-3 justify-center mb-8", shake && "animate-[shake_0.4s_ease-in-out]")}>
            {Array.from({ length }).map((_, i) => (
                <div
                    key={i}
                    className={cn(
                        "w-3.5 h-3.5 rounded-full transition-all duration-200",
                        i < filled ? "scale-110" : "scale-100",
                    )}
                    style={{
                        background: i < filled ? 'var(--ac, #7c3aed)' : 'var(--b, rgba(255,255,255,0.1))',
                    }}
                />
            ))}
        </div>
    );
}

// ─── PIN Tab ─────────────────────────────────────────────────────────────────

function PinTab({ onSuccess }: { onSuccess: () => void }) {
    const { t } = useTranslation();
    const [pin, setPin] = useState('');
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [shake, setShake] = useState(false);
    const [done, setDone] = useState(false);

    const submit = useCallback(async (fullPin: string) => {
        setLoading(true);
        setError(null);
        try {
            const name = navigator.userAgent.slice(0, 40);
            const res = await fetch(`${UM}/auth/pin/login`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({ pin: fullPin, device_name: name }),
            });
            const body = await res.json().catch(() => ({}));
            if (!res.ok) {
                setError(body.detail ?? t('auth.pinIncorrect'));
                setShake(true);
                setTimeout(() => { setShake(false); setPin(''); }, 500);
                return;
            }
            if (body.device_token) {
                try {
                    localStorage.setItem('selena_device', body.device_token);
                    document.cookie = `selena_device=${body.device_token}; max-age=${60 * 60 * 24 * 30}; path=/; samesite=strict`;
                } catch { /* ignore */ }
            }
            setDone(true);
            setTimeout(onSuccess, 600);
        } catch {
            setError(t('common.error'));
            setShake(true);
            setTimeout(() => { setShake(false); setPin(''); }, 500);
        } finally {
            setLoading(false);
        }
    }, [onSuccess, t]);

    const addDigit = useCallback((d: string) => {
        if (loading || done) return;
        setError(null);
        setPin(prev => {
            const next = prev + d;
            if (next.length >= PIN_LENGTH) {
                setTimeout(() => submit(next), 150);
            }
            return next.length <= PIN_LENGTH ? next : prev;
        });
    }, [loading, done, submit]);

    const deleteDigit = useCallback(() => {
        if (loading || done) return;
        setError(null);
        setPin(prev => prev.slice(0, -1));
    }, [loading, done]);

    // Physical keyboard support
    useEffect(() => {
        const handler = (e: globalThis.KeyboardEvent) => {
            if (loading || done) return;
            if (e.key >= '0' && e.key <= '9') addDigit(e.key);
            else if (e.key === 'Backspace') deleteDigit();
        };
        window.addEventListener('keydown', handler);
        return () => window.removeEventListener('keydown', handler);
    }, [addDigit, deleteDigit, loading, done]);

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
        <div className="flex flex-col items-center">
            <PinDots length={PIN_LENGTH} filled={pin.length} shake={shake} />

            {error && (
                <div className="flex items-center gap-2 text-xs text-red-400 mb-4 px-3 py-2 rounded-xl bg-red-500/10 border border-red-500/20">
                    <AlertCircle size={13} className="shrink-0" />
                    <span>{error}</span>
                </div>
            )}

            {loading ? (
                <div className="flex justify-center py-8">
                    <div className="w-6 h-6 border-2 border-violet-500 border-t-transparent rounded-full animate-spin" />
                </div>
            ) : (
                <NumPad onDigit={addDigit} onDelete={deleteDigit} />
            )}
        </div>
    );
}

// ─── QR Tab ──────────────────────────────────────────────────────────────────
const QR_POLL_MS = 2000;
const QR_EXPIRE_MS = 300_000;

function QrTab({ onSuccess }: { onSuccess: () => void }) {
    const { t } = useTranslation();
    const [qrImage, setQrImage] = useState<string | null>(null);
    const [joinUrl, setJoinUrl] = useState<string | null>(null);
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
        setLoading(true); setError(null); setExpired(false); setApproved(false); setQrImage(null);
        try {
            const res = await fetch(`${UM}/auth/qr/start`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ mode: 'access' }),
            });
            if (!res.ok) throw new Error('Failed');
            const data = await res.json();
            setQrImage(data.qr_image ?? null);
            setJoinUrl(data.join_url ?? null);
            setLoading(false);

            pollRef.current = setInterval(async () => {
                try {
                    const st = await fetch(`${UM}/auth/qr/status/${data.session_id}`);
                    if (st.status === 410) { stopPolling(); setExpired(true); return; }
                    if (!st.ok) return;
                    const s = await st.json();
                    if (s.status === 'complete' && s.device_token) {
                        stopPolling();
                        try {
                            if (s.session_token) sessionStorage.setItem('selena_session', s.device_token);
                            localStorage.setItem('selena_device', s.device_token);
                            document.cookie = `selena_device=${s.device_token}; max-age=${s.session_token ? 600 : 60 * 60 * 24 * 30}; path=/; samesite=strict`;
                        } catch { /* ignore */ }
                        setApproved(true);
                        setTimeout(onSuccess, 800);
                    }
                } catch { /* ignore */ }
            }, QR_POLL_MS);

            expireRef.current = setTimeout(() => { stopPolling(); setExpired(true); }, QR_EXPIRE_MS);
        } catch {
            setLoading(false);
            setError(t('common.error'));
        }
    };

    useEffect(() => { startSession(); return stopPolling; }, []); // eslint-disable-line react-hooks/exhaustive-deps

    if (approved) {
        return (
            <div className="flex flex-col items-center gap-3 py-8">
                <div className="w-14 h-14 rounded-full bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center">
                    <Check size={26} className="text-emerald-400" />
                </div>
                <p className="text-sm font-semibold" style={{ color: 'var(--tx)' }}>{t('auth.registerSuccess')}</p>
            </div>
        );
    }
    if (expired) {
        return (
            <div className="flex flex-col items-center gap-4 py-6">
                <p className="text-sm text-center" style={{ color: 'var(--tx2)' }}>{t('auth.qrExpired')}</p>
                <button onClick={startSession}
                    className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium bg-violet-600 hover:bg-violet-500 text-white">
                    <RefreshCw size={14} /> {t('auth.qrRefresh')}
                </button>
            </div>
        );
    }
    if (loading) {
        return <div className="flex justify-center py-12">
            <div className="w-8 h-8 border-2 border-violet-500 border-t-transparent rounded-full animate-spin" />
        </div>;
    }

    return (
        <div className="flex flex-col items-center gap-4">
            {qrImage ? (
                <div className="p-2 rounded-xl bg-white"><img src={qrImage} alt="QR" className="w-48 h-48" /></div>
            ) : (
                <div className="w-48 h-48 rounded-xl border-2 border-dashed flex items-center justify-center"
                    style={{ borderColor: 'var(--b)' }}><QrCode size={40} style={{ color: 'var(--tx3)' }} /></div>
            )}
            <div className="text-center space-y-1">
                <p className="text-sm font-medium" style={{ color: 'var(--tx)' }}>{t('auth.qrScanTitle')}</p>
                <p className="text-xs" style={{ color: 'var(--tx3)' }}>{t('auth.qrScanDesc')}</p>
            </div>
            <div className="flex items-center gap-2 text-xs" style={{ color: 'var(--tx3)' }}>
                <span className="w-2 h-2 rounded-full bg-violet-400 animate-pulse" />
                {t('auth.qrWaiting')}
            </div>
            {joinUrl && (
                <p className="text-xs text-center break-all" style={{ color: 'var(--tx3)' }}>
                    {t('auth.qrOrOpen')}{' '}
                    <a href={joinUrl} target="_blank" rel="noreferrer" className="text-violet-400 hover:underline">{t('auth.qrOpenLink')}</a>
                </p>
            )}
            {error && <p className="text-xs text-red-400">{error}</p>}
        </div>
    );
}

// ─── Setup Tab (first boot) ──────────────────────────────────────────────────

function SetupTab({ onSuccess }: { onSuccess: () => void }) {
    const { t } = useTranslation();
    const [username, setUsername] = useState('');
    const [displayName, setDisplayName] = useState('');
    const [pin, setPin] = useState('');
    const [confirmPin, setConfirmPin] = useState('');
    const [showPin, setShowPin] = useState(false);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [done, setDone] = useState(false);

    const canSubmit = username.trim().length > 0 && pin.length >= 4 && pin === confirmPin && !loading;

    const submit = async () => {
        if (!canSubmit) return;
        if (pin !== confirmPin) { setError(t('auth.pinMismatch')); return; }
        setLoading(true); setError(null);
        try {
            const res = await fetch(`${UM}/auth/setup`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({
                    username: username.trim(),
                    pin,
                    device_name: displayName.trim() || username.trim(),
                }),
            });
            const body = await res.json().catch(() => ({}));
            if (!res.ok) { setError(body.detail ?? t('common.error')); return; }
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
                <p className="text-sm font-semibold" style={{ color: 'var(--tx)' }}>{t('auth.setupDone')}</p>
            </div>
        );
    }

    const field = (
        label: string, value: string, onChange: (v: string) => void,
        opts?: { numeric?: boolean; autoComplete?: string; placeholder?: string; isPin?: boolean }
    ) => (
        <div>
            <label className="block text-xs font-medium mb-1.5" style={{ color: 'var(--tx2)' }}>{label}</label>
            <div className="relative">
                <input
                    type={opts?.isPin ? (showPin ? 'text' : 'password') : 'text'}
                    inputMode={opts?.numeric ? 'numeric' : undefined}
                    pattern={opts?.numeric ? '[0-9]*' : undefined}
                    maxLength={opts?.numeric ? 8 : undefined}
                    value={value}
                    onChange={(e) => { onChange(opts?.numeric ? e.target.value.replace(/\D/g, '') : e.target.value); setError(null); }}
                    onKeyDown={handleKey}
                    disabled={loading}
                    autoComplete={opts?.autoComplete}
                    className="w-full rounded-xl px-3 py-2.5 pr-10 text-sm focus:outline-none transition-all"
                    style={{ background: 'var(--bg)', border: `1px solid ${error ? '#ef4444' : 'var(--b)'}`, color: 'var(--tx)' }}
                    placeholder={opts?.placeholder ?? ''}
                />
            </div>
        </div>
    );

    return (
        <div className="space-y-3">
            {field(t('auth.username'), username, setUsername, { autoComplete: 'username', placeholder: t('auth.usernamePlaceholder') })}
            {field(t('auth.displayName'), displayName, setDisplayName, { autoComplete: 'name', placeholder: t('auth.displayNamePlaceholder') })}
            {field(t('auth.pin'), pin, setPin, { numeric: true, autoComplete: 'new-password', placeholder: '••••', isPin: true })}
            {field(t('auth.confirmPin'), confirmPin, setConfirmPin, { numeric: true, autoComplete: 'new-password', placeholder: '••••', isPin: true })}
            {pin.length >= 4 && confirmPin.length >= 4 && pin !== confirmPin && (
                <p className="text-xs text-red-400">{t('auth.pinMismatch')}</p>
            )}
            {error && (
                <div className="flex items-center gap-2 text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-xl px-3 py-2.5">
                    <AlertCircle size={13} className="shrink-0" /><span>{error}</span>
                </div>
            )}
            <button onClick={submit} disabled={!canSubmit}
                className={cn(
                    'w-full py-3 rounded-xl text-sm font-semibold text-white transition-all mt-1',
                    'bg-violet-600 hover:bg-violet-500 active:scale-[0.98]',
                    'disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2',
                )}>
                {loading
                    ? <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                    : t('auth.setupCreate')}
            </button>
        </div>
    );
}

// ─── AuthWall shell ──────────────────────────────────────────────────────────
type Tab = 'pin' | 'qr';

export default function AuthWall() {
    const { t } = useTranslation();
    const initAuth = useStore((s) => s.initAuth);
    const [tab, setTab] = useState<Tab>('pin');
    const [globalDone, setGlobalDone] = useState(false);
    const [setupRequired, setSetupRequired] = useState<boolean | null>(null);

    useEffect(() => {
        fetch(`${UM}/auth/status`)
            .then(r => r.json())
            .then(d => setSetupRequired(Boolean(d.setup_required)))
            .catch(() => setSetupRequired(false));
    }, []);

    const handleSuccess = () => {
        setGlobalDone(true);
        setTimeout(() => initAuth(), 800);
    };

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ background: 'var(--bg)' }}>
            {/* Background grid */}
            <div className="absolute inset-0 opacity-[0.03] pointer-events-none"
                style={{
                    backgroundImage: 'radial-gradient(circle at 1px 1px, var(--tx) 1px, transparent 0)',
                    backgroundSize: '28px 28px',
                }}
            />
            <div className="absolute w-96 h-96 rounded-full bg-violet-600/10 blur-3xl pointer-events-none" />

            <AnimatePresence mode="wait">
                {globalDone ? (
                    <motion.div key="done" initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }}
                        className="flex flex-col items-center gap-4">
                        <div className="w-16 h-16 rounded-full bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center">
                            <Check size={28} className="text-emerald-400" />
                        </div>
                        <p className="text-base font-semibold" style={{ color: 'var(--tx)' }}>{t('auth.registerSuccess')}</p>
                    </motion.div>
                ) : (
                    <motion.div key="card" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: -8 }} className="relative z-10 w-full max-w-sm mx-4">
                        <div className="rounded-2xl border shadow-2xl overflow-hidden"
                            style={{ background: 'var(--sf)', borderColor: 'var(--b)' }}>
                            {/* Header */}
                            <div className="px-8 pt-8 pb-4">
                                <div className="flex flex-col items-center gap-3 mb-6">
                                    <div style={{
                                        width: 48, height: 48,
                                        background: setupRequired ? 'var(--ac2, #7c3aed)' : 'var(--ac)',
                                        borderRadius: 14,
                                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                                    }}>
                                        {setupRequired
                                            ? <UserPlus size={22} color="white" />
                                            : <svg viewBox="0 0 14 14" fill="none" width="22" height="22">
                                                <path d="M7 1L2 4v5.5L7 13l5-3.5V4L7 1Z" stroke="white" strokeWidth="1.3" strokeLinejoin="round" />
                                                <circle cx="7" cy="7" r="1.8" fill="white" />
                                            </svg>
                                        }
                                    </div>
                                    <div className="text-center">
                                        <p className="text-lg font-bold" style={{ color: 'var(--tx)' }}>
                                            {setupRequired ? t('auth.setupTitle') : 'SmartHome'}
                                        </p>
                                        <p className="text-sm" style={{ color: 'var(--tx3)' }}>
                                            {setupRequired ? t('auth.setupSubtitle') : t('auth.wallPinHint')}
                                        </p>
                                    </div>
                                </div>

                                {/* Tab switcher — only for normal mode */}
                                {!setupRequired && (
                                    <div className="flex rounded-xl p-1 mb-4" style={{ background: 'var(--bg)' }}>
                                        {([['pin', KeyRound, 'auth.tabPin'], ['qr', QrCode, 'auth.tabQr']] as const).map(
                                            ([id, Icon, key]) => (
                                                <button key={id} onClick={() => setTab(id)}
                                                    className={cn(
                                                        'flex-1 flex items-center justify-center gap-1.5 py-2 rounded-lg text-xs font-medium transition-all',
                                                        tab === id ? 'bg-violet-600 text-white shadow' : 'hover:opacity-80',
                                                    )}
                                                    style={tab !== id ? { color: 'var(--tx2)' } : {}}>
                                                    <Icon size={13} />
                                                    {t(key)}
                                                </button>
                                            )
                                        )}
                                    </div>
                                )}
                            </div>

                            {/* Content */}
                            <div className="px-8 pb-8">
                                {setupRequired === null ? (
                                    <div className="flex justify-center py-8">
                                        <div className="w-6 h-6 border-2 border-violet-500 border-t-transparent rounded-full animate-spin" />
                                    </div>
                                ) : setupRequired ? (
                                    <SetupTab onSuccess={handleSuccess} />
                                ) : (
                                    <AnimatePresence mode="wait">
                                        <motion.div key={tab}
                                            initial={{ opacity: 0, x: tab === 'pin' ? -12 : 12 }}
                                            animate={{ opacity: 1, x: 0 }}
                                            exit={{ opacity: 0 }}
                                            transition={{ duration: 0.15 }}>
                                            {tab === 'pin'
                                                ? <PinTab onSuccess={handleSuccess} />
                                                : <QrTab onSuccess={handleSuccess} />}
                                        </motion.div>
                                    </AnimatePresence>
                                )}
                            </div>
                        </div>

                        <p className="text-center text-xs mt-4" style={{ color: 'var(--tx3)' }}>
                            {t('auth.wallFooter')}
                        </p>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    );
}
