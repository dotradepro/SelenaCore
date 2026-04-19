import { motion, AnimatePresence } from 'motion/react';
import { X } from 'lucide-react';
import { useStore } from '../store/useStore';
import { ElevationPanel } from './KioskElevationGate';

interface Props {
    isOpen: boolean;
    onClose: () => void;
    /** Called with the elevated_token when PIN/QR is confirmed successfully. */
    onSuccess?: (elevatedToken: string) => void;
}

/**
 * Modal wrapper around the shared `ElevationPanel`. Used by
 * `useElevated().requestElevation()` callers (Dashboard edit mode,
 * UsersPanel destructive ops, etc.) so the PIN prompt looks identical
 * to the full-page KioskElevationGate lock screen — same numpad, same
 * QR tab — without duplicating UI.
 */
export default function PinConfirmModal({ isOpen, onClose, onSuccess }: Props) {
    const setElevatedToken = useStore((s) => s.setElevatedToken);

    const handleSuccess = (token: string) => {
        setElevatedToken(token);
        try { sessionStorage.setItem('selena_elevated', token); } catch { /* ignore */ }
        onSuccess?.(token);
        onClose();
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

                    {/* Modal — same ElevationPanel as the full-page gate */}
                    <motion.div
                        initial={{ opacity: 0, scale: 0.95, y: 8 }}
                        animate={{ opacity: 1, scale: 1, y: 0 }}
                        exit={{ opacity: 0, scale: 0.95, y: 8 }}
                        transition={{ duration: 0.15 }}
                        className="relative z-10 w-full max-w-xs"
                    >
                        <ElevationPanel
                            onSuccess={handleSuccess}
                            onBack={onClose}
                            titleKey="auth.confirmTitle"
                            descKey="auth.confirmDesc"
                        />
                        {/* Close button — absolute, overlays the panel's top-right */}
                        <button
                            onClick={onClose}
                            aria-label="Close"
                            className="absolute top-3 right-3 p-1.5 rounded-lg transition-colors z-10"
                            style={{ color: 'var(--tx3)', background: 'transparent' }}
                            onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--sf2)'; }}
                            onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
                        >
                            <X size={16} />
                        </button>
                    </motion.div>
                </div>
            )}
        </AnimatePresence>
    );
}
