import React, { useState, useEffect, useCallback, useRef } from 'react';
import { cn } from '../lib/utils';
import { Delete, CornerDownLeft, ChevronUp, Globe, X, Eye, EyeOff } from 'lucide-react';

type KeyboardLayout = 'en' | 'uk';
type KeyboardMode = 'lower' | 'upper' | 'symbols';

const LAYOUTS: Record<KeyboardLayout, Record<KeyboardMode, string[][]>> = {
    en: {
        lower: [
            ['q', 'w', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p'],
            ['a', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l'],
            ['{shift}', 'z', 'x', 'c', 'v', 'b', 'n', 'm', '{bksp}'],
            ['{sym}', '{lang}', '{space}', '.', '{enter}'],
        ],
        upper: [
            ['Q', 'W', 'E', 'R', 'T', 'Y', 'U', 'I', 'O', 'P'],
            ['A', 'S', 'D', 'F', 'G', 'H', 'J', 'K', 'L'],
            ['{shift}', 'Z', 'X', 'C', 'V', 'B', 'N', 'M', '{bksp}'],
            ['{sym}', '{lang}', '{space}', '.', '{enter}'],
        ],
        symbols: [
            ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0'],
            ['@', '#', '$', '%', '&', '*', '-', '+', '='],
            ['{abc}', '!', '"', "'", ':', ';', '/', '?', '{bksp}'],
            ['{sym2}', '{lang}', '{space}', '.', '{enter}'],
        ],
    },
    uk: {
        lower: [
            ['й', 'ц', 'у', 'к', 'е', 'н', 'г', 'ш', 'щ', 'з'],
            ['ф', 'і', 'в', 'а', 'п', 'р', 'о', 'л', 'д', 'ж'],
            ['{shift}', 'я', 'ч', 'с', 'м', 'и', 'т', 'ь', 'б', 'ю', '{bksp}'],
            ['{sym}', '{lang}', 'є', '{space}', 'х', 'ї', '{enter}'],
        ],
        upper: [
            ['Й', 'Ц', 'У', 'К', 'Е', 'Н', 'Г', 'Ш', 'Щ', 'З'],
            ['Ф', 'І', 'В', 'А', 'П', 'Р', 'О', 'Л', 'Д', 'Ж'],
            ['{shift}', 'Я', 'Ч', 'С', 'М', 'И', 'Т', 'Ь', 'Б', 'Ю', '{bksp}'],
            ['{sym}', '{lang}', 'Є', '{space}', 'Х', 'Ї', '{enter}'],
        ],
        symbols: [
            ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0'],
            ['@', '#', '$', '%', '&', '*', '-', '+', '='],
            ['{abc}', '!', '"', "'", ':', ';', '/', '?', '{bksp}'],
            ['{sym2}', '{lang}', '{space}', '.', '{enter}'],
        ],
    },
};

interface VirtualKeyboardProps {
    visible: boolean;
    onKeyPress: (key: string) => void;
    onBackspace: () => void;
    onEnter: () => void;
    onClose: () => void;
    lang?: string;
    numericOnly?: boolean;
    inputValue?: string;
    isPassword?: boolean;
    onToggleVisibility?: () => void;
    passwordVisible?: boolean;
}

export default function VirtualKeyboard({ visible, onKeyPress, onBackspace, onEnter, onClose, lang = 'en', numericOnly = false, inputValue, isPassword, onToggleVisibility, passwordVisible }: VirtualKeyboardProps) {
    const [layout, setLayout] = useState<KeyboardLayout>(lang === 'uk' ? 'uk' : 'en');
    const [mode, setMode] = useState<KeyboardMode>('lower');
    const kbRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        setLayout(lang === 'uk' ? 'uk' : 'en');
    }, [lang]);

    const handleKey = useCallback((key: string) => {
        switch (key) {
            case '{shift}':
                setMode(m => m === 'upper' ? 'lower' : 'upper');
                break;
            case '{bksp}':
                onBackspace();
                break;
            case '{enter}':
                onEnter();
                break;
            case '{space}':
                onKeyPress(' ');
                break;
            case '{sym}':
            case '{sym2}':
                setMode('symbols');
                break;
            case '{abc}':
                setMode('lower');
                break;
            case '{lang}':
                setLayout(l => l === 'en' ? 'uk' : 'en');
                setMode('lower');
                break;
            default:
                onKeyPress(key);
                if (mode === 'upper') setMode('lower');
        }
    }, [onKeyPress, onBackspace, onEnter, mode]);

    if (!visible) return null;

    if (numericOnly) {
        const numpad = [
            ['1', '2', '3'],
            ['4', '5', '6'],
            ['7', '8', '9'],
            ['{bksp}', '0', '{enter}'],
        ];
        return (
            <div ref={kbRef} className="fixed bottom-0 left-0 right-0 z-50 bg-zinc-900/98 border-t border-zinc-700 px-2 pb-2 pt-1.5 backdrop-blur-sm">
                <div className="flex justify-between items-center mb-1.5 px-1">
                    <div className="flex-1 flex items-center gap-2 min-w-0">
                        <div className="flex-1 bg-zinc-800 rounded px-2 py-1 text-sm text-zinc-100 font-mono tracking-[0.3em] truncate min-h-[28px]">
                            {inputValue ? (passwordVisible ? inputValue : '•'.repeat(inputValue.length)) : <span className="text-zinc-600">PIN</span>}
                        </div>
                        {isPassword && inputValue && (
                            <button onPointerDown={(e) => { e.preventDefault(); onToggleVisibility?.(); }} className="p-1 text-zinc-500 hover:text-zinc-300 transition-colors shrink-0">
                                {passwordVisible ? <EyeOff size={14} /> : <Eye size={14} />}
                            </button>
                        )}
                    </div>
                    <button onClick={onClose} className="p-1 text-zinc-500 hover:text-zinc-300 transition-colors shrink-0 ml-1">
                        <X size={14} />
                    </button>
                </div>
                <div className="max-w-[200px] mx-auto space-y-1">
                    {numpad.map((row, ri) => (
                        <div key={ri} className="flex gap-1 justify-center">
                            {row.map(key => (
                                <button
                                    key={key}
                                    onPointerDown={(e) => { e.preventDefault(); handleKey(key); }}
                                    className={cn(
                                        "h-10 rounded-md text-sm font-medium transition-colors active:bg-zinc-600 select-none",
                                        key === '{bksp}' || key === '{enter}'
                                            ? "w-16 bg-zinc-800 text-zinc-400"
                                            : "w-14 bg-zinc-800 text-zinc-100 hover:bg-zinc-700"
                                    )}
                                >
                                    {key === '{bksp}' ? <Delete size={16} className="mx-auto" /> :
                                        key === '{enter}' ? <CornerDownLeft size={16} className="mx-auto" /> :
                                            key}
                                </button>
                            ))}
                        </div>
                    ))}
                </div>
            </div>
        );
    }

    const rows = LAYOUTS[layout][mode];

    return (
        <div ref={kbRef} className="fixed bottom-0 left-0 right-0 z-50 bg-zinc-900/98 border-t border-zinc-700 px-1 pb-1.5 pt-1 backdrop-blur-sm">
            <div className="flex justify-between items-center mb-1 px-1 gap-1">
                <div className="flex-1 flex items-center gap-1.5 min-w-0">
                    <span className="text-[10px] text-zinc-500 shrink-0">{layout.toUpperCase()}</span>
                    {inputValue !== undefined && (
                        <div className="flex-1 bg-zinc-800 rounded px-2 py-0.5 text-sm text-zinc-100 truncate min-h-[24px] flex items-center">
                            {inputValue ? (isPassword ? (passwordVisible ? inputValue : '•'.repeat(inputValue.length)) : inputValue) : <span className="text-zinc-600">...</span>}
                        </div>
                    )}
                    {isPassword && inputValue && (
                        <button onPointerDown={(e) => { e.preventDefault(); onToggleVisibility?.(); }} className="p-1 text-zinc-500 hover:text-zinc-300 transition-colors shrink-0">
                            {passwordVisible ? <EyeOff size={14} /> : <Eye size={14} />}
                        </button>
                    )}
                </div>
                <button onClick={onClose} className="p-1 text-zinc-500 hover:text-zinc-300 transition-colors shrink-0">
                    <X size={14} />
                </button>
            </div>
            <div className="space-y-1">
                {rows.map((row, ri) => (
                    <div key={ri} className="flex gap-[3px] justify-center">
                        {row.map((key, ki) => {
                            const isSpecial = key.startsWith('{');
                            let label: React.ReactNode = key;
                            let extraCls = "min-w-[30px] flex-1 max-w-[36px]";

                            if (key === '{shift}') {
                                label = <ChevronUp size={14} className={mode === 'upper' ? 'text-emerald-400' : ''} />;
                                extraCls = "w-10";
                            } else if (key === '{bksp}') {
                                label = <Delete size={14} />;
                                extraCls = "w-10";
                            } else if (key === '{enter}') {
                                label = <CornerDownLeft size={14} />;
                                extraCls = "w-14";
                            } else if (key === '{space}') {
                                label = '';
                                extraCls = "flex-[3]";
                            } else if (key === '{sym}' || key === '{sym2}') {
                                label = '123';
                                extraCls = "w-10 text-[10px]";
                            } else if (key === '{abc}') {
                                label = 'ABC';
                                extraCls = "w-10 text-[10px]";
                            } else if (key === '{lang}') {
                                label = <Globe size={14} />;
                                extraCls = "w-9";
                            }

                            return (
                                <button
                                    key={`${ri}-${ki}`}
                                    onPointerDown={(e) => { e.preventDefault(); handleKey(key); }}
                                    className={cn(
                                        "h-9 rounded-md text-sm font-medium transition-colors active:bg-zinc-600 flex items-center justify-center select-none",
                                        isSpecial ? "bg-zinc-800 text-zinc-400" : "bg-zinc-800 text-zinc-100 hover:bg-zinc-700",
                                        extraCls
                                    )}
                                >
                                    {label}
                                </button>
                            );
                        })}
                    </div>
                ))}
            </div>
        </div>
    );
}
