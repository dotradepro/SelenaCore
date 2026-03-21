import { useState, useEffect, useCallback } from 'react';
import { motion, AnimatePresence } from 'motion/react';

const GREETINGS = [
    { text: 'Привет', lang: 'ru' },
    { text: 'Hello', lang: 'en' },
    { text: 'Привіт', lang: 'uk' },
    { text: 'Hola', lang: 'es' },
    { text: 'Hallo', lang: 'de' },
    { text: 'Bonjour', lang: 'fr' },
    { text: 'Ciao', lang: 'it' },
    { text: '你好', lang: 'zh' },
    { text: 'مرحبا', lang: 'ar' },
    { text: 'こんにちは', lang: 'ja' },
    { text: '안녕하세요', lang: 'ko' },
    { text: 'Olá', lang: 'pt' },
    { text: 'Merhaba', lang: 'tr' },
    { text: 'Hej', lang: 'sv' },
    { text: 'Witaj', lang: 'pl' },
];

const LANGUAGES = [
    { id: 'ru', label: 'Русский', native: 'Русский' },
    { id: 'uk', label: 'Українська', native: 'Українська' },
    { id: 'en', label: 'English', native: 'English' },
];

interface Props {
    onSelect: (lang: string) => void;
}

export default function LanguageSelect({ onSelect }: Props) {
    const [greetingIndex, setGreetingIndex] = useState(0);
    const [showList, setShowList] = useState(false);
    const [selectedLang, setSelectedLang] = useState<string | null>(null);

    // Cycle through greetings
    useEffect(() => {
        if (showList) return;
        const timer = setInterval(() => {
            setGreetingIndex((i) => (i + 1) % GREETINGS.length);
        }, 2000);
        return () => clearInterval(timer);
    }, [showList]);

    const handleTap = useCallback(() => {
        if (!showList) {
            setShowList(true);
        }
    }, [showList]);

    const handleSelect = useCallback((lang: string) => {
        setSelectedLang(lang);
        // Small delay for visual feedback before transitioning
        setTimeout(() => onSelect(lang), 400);
    }, [onSelect]);

    return (
        <div
            className="fixed inset-0 bg-black flex flex-col items-center justify-center overflow-hidden select-none"
            onPointerDown={handleTap}
        >
            {/* Subtle radial gradient background */}
            <div className="absolute inset-0 bg-gradient-to-b from-zinc-950 via-black to-zinc-950" />

            {/* Animated greeting text */}
            <AnimatePresence mode="wait">
                {!showList && (
                    <motion.div
                        key="greetings"
                        className="relative z-10 flex flex-col items-center"
                        exit={{ opacity: 0, scale: 0.95 }}
                        transition={{ duration: 0.4 }}
                    >
                        <AnimatePresence mode="wait">
                            <motion.h1
                                key={greetingIndex}
                                initial={{ opacity: 0, y: 30 }}
                                animate={{ opacity: 1, y: 0 }}
                                exit={{ opacity: 0, y: -30 }}
                                transition={{ duration: 0.6, ease: 'easeInOut' }}
                                className="text-6xl sm:text-7xl md:text-8xl font-extralight tracking-tight text-white text-center"
                            >
                                {GREETINGS[greetingIndex].text}
                            </motion.h1>
                        </AnimatePresence>

                        {/* Subtle hint at the bottom */}
                        <motion.p
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 0.4 }}
                            transition={{ delay: 3, duration: 1 }}
                            className="absolute -bottom-20 text-sm text-zinc-500"
                        >
                            Tap to continue
                        </motion.p>
                    </motion.div>
                )}
            </AnimatePresence>

            {/* Language selection list */}
            <AnimatePresence>
                {showList && (
                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        transition={{ duration: 0.5, delay: 0.2 }}
                        className="relative z-10 w-full max-w-md px-6 flex flex-col items-center"
                        onPointerDown={(e) => e.stopPropagation()}
                    >
                        {/* Selena logo mark */}
                        <motion.div
                            initial={{ opacity: 0, scale: 0.8 }}
                            animate={{ opacity: 1, scale: 1 }}
                            transition={{ duration: 0.5, delay: 0.1 }}
                            className="mb-10"
                        >
                            <div className="w-16 h-16 rounded-full bg-gradient-to-br from-emerald-400 to-emerald-600 flex items-center justify-center shadow-lg shadow-emerald-500/20">
                                <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                                    <path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
                                    <polyline points="9 22 9 12 15 12 15 22" />
                                </svg>
                            </div>
                        </motion.div>

                        {/* Language buttons */}
                        <div className="w-full space-y-3">
                            {LANGUAGES.map((lang, i) => (
                                <motion.button
                                    key={lang.id}
                                    initial={{ opacity: 0, y: 20 }}
                                    animate={{ opacity: 1, y: 0 }}
                                    transition={{ duration: 0.4, delay: 0.3 + i * 0.08 }}
                                    onPointerDown={() => handleSelect(lang.id)}
                                    className={`
                    w-full py-5 px-6 rounded-2xl text-left transition-all duration-200
                    flex items-center justify-between
                    ${selectedLang === lang.id
                                            ? 'bg-emerald-500/20 border-2 border-emerald-500 scale-[0.98]'
                                            : 'bg-zinc-900/80 border-2 border-zinc-800/50 active:scale-[0.97] active:bg-zinc-800/80'
                                        }
                  `}
                                >
                                    <span
                                        className={`text-xl font-medium ${selectedLang === lang.id ? 'text-emerald-400' : 'text-white'
                                            }`}
                                    >
                                        {lang.native}
                                    </span>
                                    {selectedLang === lang.id && (
                                        <motion.div
                                            initial={{ scale: 0 }}
                                            animate={{ scale: 1 }}
                                            className="w-6 h-6 rounded-full bg-emerald-500 flex items-center justify-center"
                                        >
                                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                                                <polyline points="20 6 9 17 4 12" />
                                            </svg>
                                        </motion.div>
                                    )}
                                </motion.button>
                            ))}
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    );
}
