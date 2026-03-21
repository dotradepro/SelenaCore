import { useEffect, useState } from 'react';
import { CheckCircle2, XCircle, Wifi, Shield, Cpu, Cloud, Loader2, QrCode } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useStore } from '../store/useStore';
import type { StepStatus } from '../store/useStore';

interface QrData {
    url: string;
    matrix: boolean[][];
    size: number;
}

const STEP_ICONS: Record<string, React.FC<{ className?: string }>> = {
    internet: Wifi,
    admin_user: Shield,
    device_name: Cpu,
    platform: Cloud,
};

function QrSvg({ matrix, size }: { matrix: boolean[][]; size: number }) {
    const cellSize = 8;
    const padding = 4;
    const total = size * cellSize + padding * 2;

    return (
        <svg
            width={total}
            height={total}
            viewBox={`0 0 ${total} ${total}`}
            className="rounded-xl"
            style={{ background: '#ffffff' }}
            aria-label="QR-код для настройки"
        >
            {matrix.map((row, y) =>
                row.map((cell, x) =>
                    cell ? (
                        <rect
                            key={`${x}-${y}`}
                            x={padding + x * cellSize}
                            y={padding + y * cellSize}
                            width={cellSize}
                            height={cellSize}
                            fill="#0a0a0a"
                        />
                    ) : null
                )
            )}
        </svg>
    );
}

function StepRow({ id, step }: { id: string; step: StepStatus }) {
    const { t } = useTranslation();
    const Icon = STEP_ICONS[id] ?? Shield;
    return (
        <div className="flex items-center gap-3 py-2">
            <Icon className="w-4 h-4 text-slate-400 shrink-0" />
            <span
                className={`flex-1 text-sm ${step.done ? 'text-slate-200' : step.required ? 'text-red-300' : 'text-slate-400'}`}
            >
                {step.label}
            </span>
            {step.done ? (
                <CheckCircle2 className="w-5 h-5 text-emerald-400 shrink-0" />
            ) : (
                <XCircle className={`w-5 h-5 shrink-0 ${step.required ? 'text-red-400' : 'text-slate-600'}`} />
            )}
            {step.required && !step.done && (
                <span className="text-xs text-red-400 font-medium">{t('common.required')}</span>
            )}
        </div>
    );
}

interface Props {
    onStartWizard: () => void;
}

export default function SetupLanding({ onStartWizard }: Props) {
    const { t } = useTranslation();
    const { wizardRequirements, fetchWizardRequirements, isConfigured } = useStore();
    const [qrData, setQrData] = useState<QrData | null>(null);
    const [qrError, setQrError] = useState(false);
    const [reqLoading, setReqLoading] = useState(true);

    useEffect(() => {
        fetch('/api/ui/setup/qr')
            .then((r) => r.json())
            .then(setQrData)
            .catch(() => setQrError(true));

        setReqLoading(true);
        fetchWizardRequirements().finally(() => setReqLoading(false));

        const interval = setInterval(() => fetchWizardRequirements(), 15_000);
        return () => clearInterval(interval);
    }, [fetchWizardRequirements]);

    const req = wizardRequirements;
    const canProceed = req?.can_proceed ?? false;
    const steps = req?.steps ?? {};

    return (
        <div className="min-h-screen bg-slate-950 flex items-center justify-center p-4">
            <div className="w-full max-w-4xl flex flex-col md:flex-row rounded-2xl overflow-hidden shadow-2xl border border-slate-800">

                {/* ── LEFT PANEL: QR ── */}
                <div className="md:w-2/5 bg-slate-900 flex flex-col items-center justify-center gap-6 p-10 border-b md:border-b-0 md:border-r border-slate-800">
                    <p className="text-slate-500 text-xs uppercase tracking-widest font-semibold">
                        {t('setupLanding.mobileSetup')}
                    </p>

                    <div className="flex items-center justify-center">
                        {qrData ? (
                            <QrSvg matrix={qrData.matrix} size={qrData.size} />
                        ) : qrError ? (
                            <div className="w-44 h-44 rounded-xl bg-slate-800 flex flex-col items-center justify-center gap-2 text-slate-500">
                                <QrCode className="w-10 h-10" />
                                <span className="text-xs text-center">{t('setupLanding.qrUnavailable')}</span>
                            </div>
                        ) : (
                            <div className="w-44 h-44 rounded-xl bg-slate-800 flex items-center justify-center">
                                <Loader2 className="w-8 h-8 text-slate-600 animate-spin" />
                            </div>
                        )}
                    </div>

                    <div className="text-center">
                        <p className="text-white font-semibold text-sm">{t('setupLanding.scanForSetup')}</p>
                        {qrData && (
                            <p className="text-slate-500 text-xs mt-1 font-mono">{qrData.url}</p>
                        )}
                    </div>
                </div>

                {/* ── RIGHT PANEL: Setup ── */}
                <div className="md:w-3/5 bg-slate-950 flex flex-col justify-center gap-6 p-10">
                    <div>
                        <p className="text-slate-500 text-xs uppercase tracking-widest font-semibold mb-2">
                            {t('setupLanding.selenaCore')}
                        </p>
                        <h1 className="text-2xl font-bold text-white leading-tight">
                            {t('setupLanding.continueSetup')}
                        </h1>
                        <p className="text-slate-400 text-sm mt-2">
                            {t('setupLanding.setupDescription')}
                        </p>
                    </div>

                    {/* Requirements checklist */}
                    <div className="bg-slate-900 rounded-xl p-4 border border-slate-800">
                        <p className="text-slate-500 text-xs uppercase tracking-widest font-semibold mb-3">
                            {t('setupLanding.setupStatus')}
                        </p>

                        {reqLoading ? (
                            <div className="flex items-center gap-2 text-slate-500 py-2">
                                <Loader2 className="w-4 h-4 animate-spin" />
                                <span className="text-sm">{t('setupLanding.checking')}</span>
                            </div>
                        ) : (
                            <div className="divide-y divide-slate-800">
                                {Object.entries(steps).map(([id, step]) => (
                                    <StepRow key={id} id={id} step={step} />
                                ))}
                            </div>
                        )}
                    </div>

                    {/* Action buttons */}
                    <div className="flex flex-col gap-3">
                        <button
                            onClick={onStartWizard}
                            className="w-full py-3 rounded-xl bg-blue-600 hover:bg-blue-500 active:bg-blue-700 text-white font-semibold text-sm transition-colors"
                        >
                            {t('setupLanding.setupHere')}
                        </button>

                        {isConfigured && canProceed && (
                            <button
                                onClick={() => (window.location.href = '/')}
                                className="w-full py-3 rounded-xl bg-slate-800 hover:bg-slate-700 active:bg-slate-900 text-slate-200 font-semibold text-sm transition-colors border border-slate-700"
                            >
                                {t('setupLanding.goToDashboard')}
                            </button>
                        )}

                        {isConfigured && !canProceed && (
                            <p className="text-center text-xs text-red-400">
                                {t('setupLanding.requiredStepsIncomplete')}
                            </p>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
}
