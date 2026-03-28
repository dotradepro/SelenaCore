import { Cpu, Zap } from 'lucide-react';

/**
 * Shows a small badge indicating whether an engine runs on GPU (CUDA) or CPU.
 *
 * @param mode - "gpu", "cpu", or "cpu-only" (engine has no GPU support at all)
 */
export default function AccelBadge({ mode }: { mode: 'gpu' | 'cpu' | 'cpu-only' }) {
  if (mode === 'gpu') {
    return (
      <span style={{
        display: 'inline-flex', alignItems: 'center', gap: 3,
        fontSize: 10, fontWeight: 600, padding: '2px 6px', borderRadius: 4,
        background: 'rgba(34,197,94,0.12)', color: '#22c55e',
      }}>
        <Zap size={10} /> CUDA
      </span>
    );
  }

  if (mode === 'cpu-only') {
    return (
      <span style={{
        display: 'inline-flex', alignItems: 'center', gap: 3,
        fontSize: 10, fontWeight: 500, padding: '2px 6px', borderRadius: 4,
        background: 'rgba(113,113,122,0.12)', color: '#71717a',
      }}>
        <Cpu size={10} /> CPU only
      </span>
    );
  }

  // cpu (has GPU on system but this engine uses CPU)
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 3,
      fontSize: 10, fontWeight: 500, padding: '2px 6px', borderRadius: 4,
      background: 'rgba(59,130,246,0.12)', color: '#3b82f6',
    }}>
      <Cpu size={10} /> CPU
    </span>
  );
}
