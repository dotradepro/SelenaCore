import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Cpu, Zap, Loader2 } from 'lucide-react';
import { cn } from '../../lib/utils';
import VoskSection from './VoskSection';
import PiperSection from './PiperSection';
import LlmSection from './LlmSection';

export default function VoiceSettings() {
  const { t } = useTranslation();
  const [hw, setHw] = useState<{ gpu_detected: boolean; gpu_type: string; gpu_active: boolean; onnxruntime_gpu: boolean; force_cpu: boolean } | null>(null);
  const [toggling, setToggling] = useState(false);

  const fetchHw = useCallback(async () => {
    try {
      const res = await fetch('/api/ui/setup/hardware/status').then(r => r.json());
      setHw(res);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { fetchHw(); }, [fetchHw]);

  const toggleForceCpu = async () => {
    if (!hw) return;
    setToggling(true);
    try {
      await fetch('/api/ui/setup/hardware/gpu-override', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ force_cpu: !hw.force_cpu }),
      });
      await fetchHw();
    } catch { /* ignore */ }
    setToggling(false);
  };

  return (
    <div className="space-y-8">
      <div>
        <h3 className="text-xl font-semibold mb-1">{t('settings.voiceAssistant')}</h3>
        <p className="text-sm text-zinc-400">{t('settings.voiceAssistantDesc')}</p>
      </div>

      {/* Hardware / GPU status */}
      {hw && (
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          background: 'var(--sf)', border: '1px solid var(--b)', borderRadius: 10, padding: '10px 16px',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {hw.gpu_detected ? <Zap size={14} style={{ color: 'var(--gr)' }} /> : <Cpu size={14} style={{ color: 'var(--tx3)' }} />}
            <span style={{ fontSize: 12, color: 'var(--tx)' }}>
              {hw.gpu_detected
                ? `${t('settings.gpuDetected')}: ${hw.gpu_type === 'jetson' ? 'NVIDIA Jetson (CUDA)' : 'NVIDIA GPU (CUDA)'}`
                : t('settings.cpuOnly')
              }
            </span>
            {hw.gpu_detected && (
              <span className={cn("text-[10px] px-1.5 py-0.5 rounded font-medium",
                hw.gpu_active ? "bg-emerald-500/10 text-emerald-500" : "bg-zinc-700 text-zinc-400")}>
                {hw.gpu_active ? 'GPU Active' : 'CPU mode'}
              </span>
            )}
            {hw.gpu_active && hw.onnxruntime_gpu && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-400 font-medium">
                ONNX CUDA
              </span>
            )}
          </div>
          {hw.gpu_detected && (
            <button onClick={toggleForceCpu} disabled={toggling}
              className="text-xs px-3 py-1 rounded-lg bg-zinc-800 text-zinc-300 hover:bg-zinc-700 disabled:opacity-50 flex items-center gap-1">
              {toggling ? <Loader2 size={11} className="animate-spin" /> : null}
              {hw.force_cpu ? t('settings.enableGpu') : t('settings.forceCpu')}
            </button>
          )}
        </div>
      )}

      <VoskSection />
      <PiperSection />
      <LlmSection />
    </div>
  );
}
