import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Smartphone, X, AlertCircle, Eye, EyeOff, Check } from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { useStore } from '../store/useStore';
import { cn } from '../lib/utils';

const UM_BASE = '/api/ui/modules/user-manager';

/**
 * Shown to browsers that have no registered device_token.
 * Lets the user register with username + PIN, or dismiss.
 */
export default function DeviceAuthBanner() {
    const { t } = useTranslation();
    const user = useStore((s) => s.user);
    const initAuth = useStore((s) => s.initAuth);
    const [dismissed, setDismissed] = useState(false);
    const [open, setOpen] = useState(false);

    // Only show for unregistered (guest) browsers; not for kiosk (which sets cookie from wizard)
    const shouldShow = !dismissed && (!user || !user.authenticated);
    if (!shouldShow) return null;

    return (
        <>
            {/* Compact banner */}
            <div className="fixed bottom-4 left-1/2 -translate-x-1/2 z-40 flex items-center gap-3 bg-zinc-900 border border-zinc-700 rounded-xl px-4 py-2.5 shadow-xl max-w-sm w-[calc(100%-2rem)]">
                <Smartphone size={16} className="text-violet-400 shrink-0" />
                <span className="text-xs text-zinc-300 flex-1">{t('auth.bannerText')}</span>
                <button
                    onClick={() => setOpen(true)}
                    className="text-xs font-medium text-violet-400 hover:text-violet-300 transition-colors shrink-0"
                >
                    {t('auth.bannerAction')}
                </button>
                <button
                    onClick={() => setDismissed(true)}
                    className="text-zinc-600 hover:text-zinc-400 transition-colors"
                >
                    <X size={14} />
                </button>
            </div>

            {/* Registration modal */}
            <RegisterModal isOpen={open} onClose={() => setOpen(false)} onSuccess={initAuth} />
        </>
    );
}

interface RegisterModalProps {
    isOpen: boolean;
    onClose: () => void;
    onSuccess: () => Promise<void>;
}

function RegisterModal({ isOpen, onClose, onSuccess }: RegisterModalProps) {
    const { t } = useTranslation();
    const [username, setUsername] = useState('');
    const [pin, setPin] = useState('');
    const [deviceName, setDeviceName] = useState('');
    const [showPin, setShowPin] = useState(false);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [done, setDone] = useState(false);

    const reset = () => {
        setUsername('');
        setPin('');
        setDeviceName('');
        setShowPin(false);
        setError(null);
        setDone(false);
    };

    const register = async () => {
        if (!username || pin.length < 4) return;
        setLoading(true);
        setError(null);
        try {
            const name = deviceName.trim() || navigator.userAgent.slice(0, 30);
            const res = await fetch(`${UM_BASE}/auth/device/register`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({ username, pin, device_name: name }),
            });
            const body = await res.json().catch(() => ({}));
            if (!res.ok) {
                setError(body.detail ?? t('auth.registerFailed'));
                setPin('');
                return;
            }
            // Persist token
            if (body.device_token) {
                try {
                    localStorage.setItem('selena_device', body.device_token);
                    document.cookie = `selena_device=${body.device_token}; max-age=${60 * 60 * 24 * 30}; path=/; samesite=strict`;
                } catch { /* ignore */ }
            }
            setDone(true);
            setTimeout(async () => {
                await onSuccess();
                onClose();
                reset();
            }, 1500);
        } catch {
            setError(t('common.error'));
        } finally {
            setLoading(false);
        }
    };

    return (
        <AnimatePresence>
            {isOpen && (
                <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
                        onClick={() => { onClose(); reset(); }}
                    />
                    <motion.div
                        initial={{ opacity: 0, scale: 0.95, y: 8 }}
                        animate={{ opacity: 1, scale: 1, y: 0 }}
                        exit={{ opacity: 0, scale: 0.95, y: 8 }}
                        className="relative z-10 w-full max-w-sm bg-zinc-900 border border-zinc-800 rounded-xl shadow-2xl p-6"
                    >
                        <button
                            onClick={() => { onClose(); reset(); }}
                            className="absolute top-4 right-4 p-1 text-zinc-500 hover:text-zinc-300 transition-colors"
                        >
                            <X size={16} />
                        </button>

                        <div className="flex items-center gap-3 mb-5">
                            <div className="w-9 h-9 rounded-full bg-violet-500/10 flex items-center justify-center">
                                <Smartphone size={16} className="text-violet-400" />
                            </div>
                            <div>
                                <h2 className="text-sm font-semibold">{t('auth.registerTitle')}</h2>
                                <p className="text-xs text-zinc-500">{t('auth.registerDesc')}</p>
                            </div>
                        </div>

                        {done ? (
                            <div className="flex flex-col items-center gap-3 py-4">
                                <div className="w-10 h-10 rounded-full bg-emerald-500/10 flex items-center justify-center">
                                    <Check size={18} className="text-emerald-400" />
                                </div>
                                <p className="text-sm text-emerald-400">{t('auth.registerSuccess')}</p>
                            </div>
                        ) : (
                            <div className="space-y-3">
                                <div>
                                    <label className="block text-xs font-medium text-zinc-400 mb-1">{t('auth.username')}</label>
                                    <input
                                        type="text"
                                        value={username}
                                        onChange={(e) => { setUsername(e.target.value); setError(null); }}
                                        disabled={loading}
                                        className="w-full bg-zinc-950 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-50 focus:outline-none focus:border-violet-500 transition-all"
                                        placeholder={t('auth.usernamePlaceholder')}
                                    />
                                </div>
                                <div>
                                    <label className="block text-xs font-medium text-zinc-400 mb-1">{t('auth.pin')}</label>
                                    <div className="relative">
                                        <input
                                            type={showPin ? 'text' : 'password'}
                                            inputMode="numeric"
                                            pattern="[0-9]*"
                                            maxLength={8}
                                            value={pin}
                                            onChange={(e) => { setPin(e.target.value.replace(/\D/g, '')); setError(null); }}
                                            disabled={loading}
                                            className={cn(
                                                'w-full bg-zinc-950 border rounded-lg px-3 py-2 pr-9 text-sm font-mono tracking-widest text-zinc-50 focus:outline-none transition-all',
                                                error ? 'border-red-500' : 'border-zinc-700 focus:border-violet-500'
                                            )}
                                            placeholder="••••"
                                        />
                                        <button
                                            type="button"
                                            onClick={() => setShowPin((p) => !p)}
                                            className="absolute right-2 top-1/2 -translate-y-1/2 text-zinc-600 hover:text-zinc-400 transition-colors"
                                        >
                                            {showPin ? <EyeOff size={13} /> : <Eye size={13} />}
                                        </button>
                                    </div>
                                </div>
                                <div>
                                    <label className="block text-xs font-medium text-zinc-400 mb-1">{t('auth.deviceName')}</label>
                                    <input
                                        type="text"
                                        value={deviceName}
                                        onChange={(e) => setDeviceName(e.target.value)}
                                        disabled={loading}
                                        className="w-full bg-zinc-950 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-50 focus:outline-none focus:border-violet-500 transition-all"
                                        placeholder={t('auth.deviceNamePlaceholder')}
                                    />
                                </div>

                                {error && (
                                    <div className="flex items-center gap-2 text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">
                                        <AlertCircle size={12} className="shrink-0" />
                                        <span>{error}</span>
                                    </div>
                                )}

                                <button
                                    onClick={register}
                                    disabled={loading || !username || pin.length < 4}
                                    className="w-full py-2.5 rounded-lg bg-violet-600 hover:bg-violet-500 text-white text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
                                >
                                    {loading ? (
                                        <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                                    ) : (
                                        t('auth.register')
                                    )}
                                </button>
                            </div>
                        )}
                    </motion.div>
                </div>
            )}
        </AnimatePresence>
    );
}
