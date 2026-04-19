import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, AlertCircle, RefreshCw, ChevronRight } from 'lucide-react';
import { motion } from 'framer-motion';

function cn(...classes: (string | false | undefined | null)[]) {
  return classes.filter(Boolean).join(' ');
}

interface ProvisionTask {
  id: string;
  label: string;
  status: string;
  error?: string;
  /** Fine-grained outcome (e.g. "installed" / "skipped" / "failed") —
   *  used by tasks where "done" hides important nuance (e.g.
   *  install_native_services skipping in-container installs). */
  outcome?: string;
  /** Human-readable secondary line shown when `outcome` is present. */
  message?: string;
  progress?: {
    downloaded_bytes: number;
    total_bytes: number;
  };
}

interface ProvisionProgressProps {
  /** Called when provisioning finishes successfully. */
  onDone?: () => void;
  /** Called when user clicks retry after failure. */
  onRetry?: () => void;
  /** Called when user clicks skip after failure (optional). */
  onSkip?: () => void;
  /** Show the "Continue" / "Skip" buttons. Default true. */
  showActions?: boolean;
  /** Extra CSS class for the container. */
  className?: string;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export default function ProvisionProgress({
  onDone,
  onRetry,
  onSkip,
  showActions = true,
  className,
}: ProvisionProgressProps) {
  const { t } = useTranslation();
  const [tasks, setTasks] = useState<ProvisionTask[]>([]);
  const [done, setDone] = useState(false);
  const [failed, setFailed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [completed, setCompleted] = useState(0);
  const [total, setTotal] = useState(0);
  const [started, setStarted] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const startProvision = async () => {
    setStarted(true);
    setDone(false);
    setFailed(false);
    setError(null);
    try {
      const res = await fetch('/api/ui/setup/provision', { method: 'POST' });
      const data = await res.json();
      if (data.status === 'already_running') {
        // Attach to an already running provisioning pipeline
      }
      setTasks(data.tasks || []);
      setTotal(data.total || 0);
      setCompleted(data.completed || 0);

      pollRef.current = setInterval(async () => {
        try {
          const sr = await fetch('/api/ui/setup/provision/status');
          const sd = await sr.json();
          setTasks(sd.tasks || []);
          setCompleted(sd.completed || 0);
          setTotal(sd.total || 0);
          if (sd.done || sd.failed) {
            if (pollRef.current) clearInterval(pollRef.current);
            pollRef.current = null;
            setDone(sd.done);
            setFailed(sd.failed);
            if (sd.error) setError(sd.error);
          }
        } catch { /* retry next interval */ }
      }, 1500);
    } catch (e: any) {
      setFailed(true);
      setError(e.message || 'Provisioning failed to start');
    }
  };

  useEffect(() => {
    startProvision();
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  // Compute overall download progress percentage
  const overallProgress = (() => {
    if (total === 0) return 0;
    // For tasks with byte-level progress, use bytes; otherwise task-level
    let totalBytes = 0;
    let downloadedBytes = 0;
    let hasProgress = false;
    for (const tk of tasks) {
      if (tk.progress && tk.progress.total_bytes > 0) {
        hasProgress = true;
        totalBytes += tk.progress.total_bytes;
        downloadedBytes += Math.min(tk.progress.downloaded_bytes, tk.progress.total_bytes);
      }
    }
    if (hasProgress && totalBytes > 0) {
      // Weighted: download tasks by bytes, non-download tasks count as done/not-done
      const downloadPct = downloadedBytes / totalBytes;
      const nonDownloadTasks = tasks.filter(tk => !tk.progress || tk.progress.total_bytes === 0);
      const nonDownloadDone = nonDownloadTasks.filter(tk => tk.status === 'done' || tk.status === 'error').length;
      const nonDownloadTotal = nonDownloadTasks.length || 1;
      // Weight: downloads = 80% of overall, non-downloads = 20%
      return downloadPct * 0.8 + (nonDownloadDone / nonDownloadTotal) * 0.2;
    }
    return completed / total;
  })();

  const pctText = Math.round(overallProgress * 100);
  const arcLength = overallProgress * 264;

  return (
    <div className={cn("flex flex-col items-center justify-center gap-6", className)}>
      {/* Animated progress ring */}
      <div className="relative w-24 h-24">
        <svg className="w-full h-full" viewBox="0 0 100 100" style={{ transform: 'rotate(-90deg)' }}>
          <circle cx="50" cy="50" r="42" fill="none" stroke="#27272a" strokeWidth="6" />
          <circle cx="50" cy="50" r="42" fill="none" stroke="#10b981" strokeWidth="6"
            strokeLinecap="round"
            strokeDasharray={`${done ? 264 : arcLength} 264`}
            className="transition-all duration-700"
          />
        </svg>
        <div className="absolute inset-0 flex items-center justify-center">
          {done ? (
            <Check size={32} className="text-emerald-500" />
          ) : failed ? (
            <AlertCircle size={32} className="text-red-400" />
          ) : (
            <span className="text-sm font-bold text-emerald-500">
              {pctText}%
            </span>
          )}
        </div>
      </div>

      {/* Title */}
      <div className="text-center">
        <h2 className="text-lg font-semibold mb-1">
          {done ? t('wizard.provisionDone') : failed ? t('wizard.provisionFailed') : t('wizard.provisionTitle')}
        </h2>
        <p className="text-xs text-zinc-400">
          {done ? t('wizard.provisionDoneDesc') : failed ? (error || t('wizard.provisionFailedDesc')) : t('wizard.provisionDesc')}
        </p>
      </div>

      {/* Task list */}
      <div className="w-full max-w-sm space-y-2">
        {tasks.map((tk) => {
          const hasProg = tk.progress && tk.progress.total_bytes > 0;
          const taskPct = hasProg
            ? Math.round((tk.progress!.downloaded_bytes / tk.progress!.total_bytes) * 100)
            : 0;
          return (
            <div
              key={tk.id}
              className={cn(
                "px-3 py-2.5 rounded-lg border transition-all",
                tk.status === 'running' ? "border-emerald-500/50 bg-emerald-500/5" :
                  tk.status === 'done' ? "border-zinc-800 bg-zinc-900/50 opacity-70" :
                    tk.status === 'error' ? "border-red-500/30 bg-red-500/5" :
                      "border-zinc-800/50 bg-zinc-900/30 opacity-40"
              )}
            >
              <div className="flex items-center gap-3">
                <div className="shrink-0 w-5 h-5 flex items-center justify-center">
                  {tk.status === 'running' ? (
                    <div className="w-4 h-4 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin" />
                  ) : tk.status === 'done' ? (
                    <Check size={16} className="text-emerald-500" />
                  ) : tk.status === 'error' ? (
                    <AlertCircle size={14} className="text-red-400" />
                  ) : (
                    <div className="w-2 h-2 rounded-full bg-zinc-600" />
                  )}
                </div>
                <div className="flex-1 min-w-0">
                  <span className={cn(
                    "text-sm font-medium",
                    tk.status === 'running' && "text-emerald-400",
                    tk.outcome === 'skipped' && "text-zinc-400",
                  )}>
                    {t(`wizard.provTask_${tk.label}` as any)}
                  </span>
                  {/* outcome: skipped / failed carry an explanatory message
                      (e.g. "Host-managed"). done tasks without outcome stay
                      silent — only the checkmark conveys completion. */}
                  {tk.outcome && tk.message && (
                    <p className={cn(
                      "text-[10px] mt-0.5 truncate",
                      tk.outcome === 'skipped' ? "text-zinc-500" :
                        tk.outcome === 'failed' ? "text-red-400" :
                          "text-emerald-400/80"
                    )}>
                      {tk.outcome === 'skipped' ? '⏭ ' : tk.outcome === 'failed' ? '✕ ' : '✓ '}
                      {tk.message}
                    </p>
                  )}
                  {tk.error && !tk.message && <p className="text-[10px] text-red-400 mt-0.5 truncate">{tk.error}</p>}
                </div>
                {/* Percentage badge for running downloads */}
                {tk.status === 'running' && hasProg && (
                  <span className="text-xs font-mono text-emerald-400">{taskPct}%</span>
                )}
              </div>
              {/* Progress bar for running downloads */}
              {tk.status === 'running' && hasProg && (
                <div className="mt-2 ml-8">
                  <div className="w-full h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-emerald-500 rounded-full transition-all duration-500"
                      style={{ width: `${taskPct}%` }}
                    />
                  </div>
                  <p className="text-[10px] text-zinc-500 mt-1">
                    {formatBytes(tk.progress!.downloaded_bytes)} / {formatBytes(tk.progress!.total_bytes)}
                  </p>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Action buttons */}
      {showActions && done && (
        <motion.button
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          onClick={onDone}
          className="px-6 py-2.5 rounded-lg text-sm font-medium bg-emerald-500 text-zinc-950 hover:bg-emerald-400 transition-colors flex items-center gap-2"
        >
          {t('wizard.provisionContinue')}
          <ChevronRight size={16} />
        </motion.button>
      )}
      {showActions && failed && (
        <div className="flex gap-3">
          <button
            onClick={() => { startProvision(); onRetry?.(); }}
            className="px-5 py-2 rounded-lg text-xs font-medium bg-zinc-800 text-zinc-50 hover:bg-zinc-700 transition-colors flex items-center gap-2"
          >
            <RefreshCw size={14} />
            {t('common.retry') || 'Retry'}
          </button>
          {onSkip && (
            <button
              onClick={onSkip}
              className="px-5 py-2 rounded-lg text-xs font-medium text-zinc-400 hover:text-zinc-50"
            >
              {t('common.skip') || 'Skip'}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
