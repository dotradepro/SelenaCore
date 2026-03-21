import { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { useTranslation } from 'react-i18next';

const GREETINGS = [
    'Привет', 'Hello', 'Привіт', 'Hola', 'Hallo',
    'Bonjour', 'Ciao', '你好', 'مرحبا', 'こんにちは',
    '안녕하세요', 'Olá', 'Merhaba', 'Hej', 'Witaj',
];

/* ── QR SVG renderer ── */
interface QrData { url: string; matrix: boolean[][]; size: number }

function QrSvg({ matrix, size }: { matrix: boolean[][]; size: number }) {
    const cell = 6;
    const pad = 3;
    const total = size * cell + pad * 2;
    return (
        <svg width={total} height={total} viewBox={`0 0 ${total} ${total}`} className="rounded-xl" style={{ background: '#fff' }}>
            {matrix.map((row, y) =>
                row.map((on, x) =>
                    on ? <rect key={`${x}-${y}`} x={pad + x * cell} y={pad + y * cell} width={cell} height={cell} fill="#0a0a0a" /> : null
                )
            )}
        </svg>
    );
}

interface Props {
    onContinue: () => void;
}

export default function LanguageSelect({ onContinue }: Props) {
    const { t } = useTranslation();
    const [gi, setGi] = useState(0);
    const [qr, setQr] = useState<QrData | null>(null);

    useEffect(() => {
        const t = setInterval(() => setGi(i => (i + 1) % GREETINGS.length), 2000);
        return () => clearInterval(t);
    }, []);

    useEffect(() => {
        fetch('/api/ui/setup/qr')
            .then(r => r.json())
            .then(data => { if (data?.matrix && data?.size) setQr(data); })
            .catch(() => { });
    }, []);

    return (
        <div
            className="fixed inset-0 bg-black flex flex-col items-center justify-center select-none overflow-hidden"
            onPointerDown={onContinue}
        >
            <div className="absolute inset-0 bg-gradient-to-b from-zinc-950 via-black to-zinc-950" />

            <div className="relative z-10 flex flex-col items-center gap-8">
                {/* QR code */}
                <motion.div
                    initial={{ opacity: 0, scale: 0.9 }}
                    animate={{ opacity: 1, scale: 1 }}
                    transition={{ duration: 0.6 }}
                    className="flex flex-col items-center"
                >
                    {qr ? (
                        <QrSvg matrix={qr.matrix} size={qr.size} />
                    ) : (
                        <div className="w-44 h-44 rounded-xl bg-zinc-900 border border-zinc-800 flex items-center justify-center">
                            <div className="w-6 h-6 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin" />
                        </div>
                    )}
                    <p className="text-zinc-400 text-sm font-medium mt-4">{t('languageSelect.scanForSetup')}</p>
                    {qr && <p className="text-zinc-700 text-xs mt-1 font-mono">{qr.url}</p>}
                </motion.div>

                {/* Cycling greetings */}
                <div className="h-20 flex items-center justify-center">
                    <AnimatePresence mode="wait">
                        <motion.h1
                            key={gi}
                            initial={{ opacity: 0, y: 24 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0, y: -24 }}
                            transition={{ duration: 0.5, ease: 'easeInOut' }}
                            className="text-5xl md:text-7xl font-extralight tracking-tight text-white text-center"
                        >
                            {GREETINGS[gi]}
                        </motion.h1>
                    </AnimatePresence>
                </div>

                {/* Tap hint */}
                <motion.p
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={{ delay: 2, duration: 1 }}
                    className="text-zinc-500 text-sm"
                >
                    {t('languageSelect.tapToContinue')}
                </motion.p>
            </div>
        </div>
    );
}
