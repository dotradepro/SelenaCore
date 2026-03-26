import { useState, useEffect, useRef, type KeyboardEvent } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { useTranslation } from 'react-i18next';
import { X, Lock, AlertCircle, Eye, EyeOff } from 'lucide-react';
import { useStore } from '../store/useStore';
import { cn } from '../lib/utils';

interface Props {
    isOpen: boolean;
    onClose: () => void;
    /** Called with the elevated_token when PIN is confirmed successfully. */
    onSuccess?: (elevatedToken: string) => void;
}

const UM_BASE = '/api/ui/modules/user-manager';

export default function PinConfirmModal({ isOpen, onClose, onSuccess }: Props) {
    const { t } = useTranslation();
    const user = useStore((s) => s.user);
    const setElevatedToken = useStore((s) => s.setElevatedToken);
    const [pin, setPin] = useState('');
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [showPin, setShowPin] = useState(false);
    const inputRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        if (isOpen) {
            setPin('');
            setError(null);
            setShowPin(false);
            setTimeout(() => inputRef.current?.focus(), 100);
        }
    }, [isOpen]);

    const getDeviceToken = () => {
        try {
            const cookieMatch = document.cookie.match(/(?:^|;\s*)selena_device=([^;]+)/);
            return sessionStorage.getItem('selena_session') ?? cookieMatch?.[1] ?? localStorage.getItem('selena_device') ?? '';
        } catch {
            return '';
        }
    };

    const submit = async () => {
        if (pin.length < 4) return;
        setLoading(true);
        setError(null);
        try {
            const res = await fetch(`${UM_BASE}/auth/pin/confirm`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Device-Token': getDeviceToken(),
                },
                credentials: 'include',
                body: JSON.stringify({ pin }),
            });
            if (!res.ok) {
                const body = await res.json().catch(() => ({}));
                setError(body.detail ?? t('auth.pinIncorrect'));
                setPin('');
                inputRef.current?.focus();
                return;
            }
            const data = await res.json();
            setElevatedToken(data.elevated_token);
            onSuccess?.(data.elevated_token);
            onClose();
        } catch {
            setError(t('common.error'));
        } finally {
            setLoading(false);
        }
    };

    const handleKey = (e: KeyboardEvent) => {
        if (e.key === 'Enter') submit();
        if (e.key === 'Escape') onClose();
    };

    return (
        <AnimatePresence>
            {isOpen && (
                <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
                    {/* Backdrop */}
                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
                        onClick={onClose}
                    />

                    {/* Modal */}
                    <motion.div
                        initial={{ opacity: 0, scale: 0.95, y: 8 }}
                        animate={{ opacity: 1, scale: 1, y: 0 }}
                        exit={{ opacity: 0, scale: 0.95, y: 8 }}
                        transition={{ duration: 0.15 }}
                        className="relative z-10 w-full max-w-sm bg-zinc-900 border border-zinc-800 rounded-xl shadow-2xl p-6"
                    >
                        {/* Close */}
                        <button
                            onClick={onClose}
                            className="absolute top-4 right-4 p-1 text-zinc-500 hover:text-zinc-300 transition-colors"
                        >
                            <X size={16} />
                        </button>

                        {/* Icon + title */}
                        <div className="flex flex-col items-center gap-3 mb-5">
                            <div className="w-10 h-10 rounded-full bg-violet-500/10 flex items-center justify-center">
                                <Lock size={18} className="text-violet-400" />
                            </div>
                            <div className="text-center">
                                <h2 className="text-base font-semibold">{t('auth.confirmTitle')}</h2>
                                {user?.name && (
                                    <p className="text-xs text-zinc-500 mt-0.5">
                                        {t('auth.confirmAs', { name: user.name })}
                                    </p>
                                )}
                            </div>
                        </div>

                        {/* PIN input */}
                        <div className="relative mb-4">
                            <input
                                ref={inputRef}
                                type={showPin ? 'text' : 'password'}
                                inputMode="numeric"
                                pattern="[0-9]*"
                                maxLength={8}
                                value={pin}
                                onChange={(e) => {
                                    setPin(e.target.value.replace(/\D/g, ''));
                                    setError(null);
                                }}
                                onKeyDown={handleKey}
                                disabled={loading}
                                className={cn(
                                    'w-full bg-zinc-950 border rounded-lg px-4 py-3 pr-10 text-center text-xl font-mono tracking-widest text-zinc-50 focus:outline-none transition-all',
                                    error
                                        ? 'border-red-500 focus:border-red-500'
                                        : 'border-zinc-700 focus:border-violet-500',
                                    loading && 'opacity-50 cursor-not-allowed'
                                )}
                                placeholder="••••"
                            />
                            <button
                                type="button"
                                onClick={() => setShowPin((p) => !p)}
                                className="absolute right-3 top-1/2 -translate-y-1/2 text-zinc-500 hover:text-zinc-300 transition-colors"
                            >
                                {showPin ? <EyeOff size={14} /> : <Eye size={14} />}
                            </button>
                        </div>

                        {/* Error */}
                        {error && (
                            <div className="flex items-center gap-2 text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2 mb-4">
                                <AlertCircle size={12} className="shrink-0" />
                                <span>{error}</span>
                            </div>
                        )}

                        {/* Action */}
                        <button
                            onClick={submit}
                            disabled={loading || pin.length < 4}
                            className="w-full py-2.5 rounded-lg bg-violet-600 hover:bg-violet-500 text-white text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
                        >
                            {loading ? (
                                <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                            ) : (
                                t('auth.confirm')
                            )}
                        </button>
                    </motion.div>
                </div>
            )}
        </AnimatePresence>
    );
}
