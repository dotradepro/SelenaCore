import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useStore } from '../store/useStore';

interface ProcessInfo {
  pid: number;
  name: string;
  user: string;
  cpu: number;
  mem_pct: number;
  ram_mb: number;
  status: string;
}

function fmtUptime(sec: number): string {
  if (!sec) return '—';
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  return `${h}h ${m}m`;
}

export default function SystemPage() {
  const { t } = useTranslation();
  const stats = useStore((s) => s.stats);
  const health = useStore((s) => s.health);
  const fetchStats = useStore((s) => s.fetchStats);

  const [processes, setProcesses] = useState<ProcessInfo[]>([]);
  const [procSort, setProcSort] = useState<'cpu' | 'ram'>('cpu');
  const [procVisible, setProcVisible] = useState(false);
  const [procLoading, setProcLoading] = useState(false);

  useEffect(() => {
    fetchStats();
    const id = setInterval(fetchStats, 15000);
    return () => clearInterval(id);
  }, [fetchStats]);

  const loadProcesses = useCallback(async (sort: 'cpu' | 'ram' = procSort) => {
    setProcLoading(true);
    try {
      const resp = await fetch(`/api/ui/system/processes?sort=${sort}&limit=30`);
      if (resp.ok) {
        const data = await resp.json();
        setProcesses(data.processes ?? []);
      }
    } catch { /* ignore */ }
    setProcLoading(false);
  }, [procSort]);

  const handleShowProcesses = useCallback(() => {
    setProcVisible(true);
    loadProcesses();
  }, [loadProcesses]);

  const handleSortChange = useCallback((sort: 'cpu' | 'ram') => {
    setProcSort(sort);
    loadProcesses(sort);
  }, [loadProcesses]);

  const showToast = useStore(s => s.showToast);
  const handleKill = useCallback(async (proc: ProcessInfo) => {
    if (!confirm(t('systemInfo.killConfirm', { name: proc.name, pid: proc.pid }))) return;
    try {
      const resp = await fetch(`/api/ui/system/processes/${proc.pid}/kill`, { method: 'POST' });
      if (resp.ok) {
        showToast(t('systemInfo.processKilled', { name: proc.name }));
        loadProcesses();
      } else {
        const err = await resp.json().catch(() => ({ detail: 'Unknown error' }));
        showToast(t('systemInfo.killFailed', { error: err.detail }), 'error');
      }
    } catch (e: unknown) {
      showToast(t('systemInfo.killFailed', { error: String(e) }), 'error');
    }
  }, [loadProcesses, t, showToast]);

  const ramPct = stats ? Math.round((stats.ramUsedMb / stats.ramTotalMb) * 100) : 0;
  const swapPct = stats && stats.swapTotalMb > 0 ? Math.round((stats.swapUsedMb / stats.swapTotalMb) * 100) : 0;
  const diskPct = stats ? Math.round((stats.diskUsedGb / stats.diskTotalGb) * 100) : 0;
  const tmpPct = stats ? Math.min(100, Math.round((stats.cpuTemp / 90) * 100)) : 0;
  const cpuLoadPct = stats ? Math.min(100, Math.round((stats.cpuLoad[0] / stats.cpuCount) * 100)) : 0;

  const ollama = stats?.ollama;

  function barColor(pct: number): string {
    if (pct < 60) return 'var(--gr)';
    if (pct < 80) return 'var(--am)';
    return 'var(--rd)';
  }

  return (
    <div className="generic-page">
      {/* Core + Network */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
        <div className="card">
          <div className="card-title">{t('systemInfo.core')}</div>
          <div className="sysrow">
            <span className="srl">{t('systemInfo.version')}</span>
            <span className="srv">{health?.version ?? stats?.version ?? '—'}</span>
          </div>
          <div className="sysrow">
            <span className="srl">{t('systemInfo.api')}</span>
            <span className="srv">:7070</span>
          </div>
          <div className="sysrow">
            <span className="srl">{t('systemInfo.ui')}</span>
            <span className="srv">:80</span>
          </div>
          <div className="sysrow">
            <span className="srl">{t('systemInfo.uptime')}</span>
            <span className="srv">{fmtUptime(stats?.uptime ?? 0)}</span>
          </div>
          <div className="sysrow">
            <span className="srl">{t('systemInfo.mode')}</span>
            <span className="srv" style={{ color: stats?.mode === 'safe_mode' ? 'var(--am)' : 'var(--tx2)' }}>
              {stats?.mode ?? 'normal'}
            </span>
          </div>
          <div className="sysrow">
            <span className="srl">{t('systemInfo.platform')}</span>
            <span className={`badge ${health?.status === 'ok' ? 'bg-run' : 'bg-stop'}`}>
              {health?.status === 'ok' ? t('systemInfo.connected') : t('systemInfo.disconnected')}
            </span>
          </div>
        </div>

        <div className="card">
          <div className="card-title">{t('systemInfo.network')}</div>
          <div className="sysrow">
            <span className="srl">{t('systemInfo.status')}</span>
            <span className={`badge ${health?.status === 'ok' ? 'bg-run' : 'bg-stop'}`}>
              {health?.status === 'ok' ? t('systemInfo.online') : t('systemInfo.offline')}
            </span>
          </div>
          <div className="sysrow">
            <span className="srl">{t('systemInfo.mdns')}</span>
            <span className="srv" style={{ color: 'var(--ac)' }}>smarthome.local</span>
          </div>
          <div className="sysrow">
            <span className="srl">{t('systemInfo.apiPort')}</span>
            <span className="srv">7070</span>
          </div>
          <div className="sysrow">
            <span className="srl">{t('systemInfo.uiPort')}</span>
            <span className="srv">80</span>
          </div>
          <div className="sysrow">
            <span className="srl">{t('systemInfo.integrity')}</span>
            <span className={`badge ${(stats?.integrity ?? 'ok') === 'ok' ? 'bg-run' : 'bg-stop'}`}>
              {stats?.integrity ?? 'ok'}
            </span>
          </div>
        </div>
      </div>

      {/* Hardware — CPU + RAM + Swap + Disk */}
      <div className="card" style={{ marginTop: 8 }}>
        <div className="card-title">{t('systemInfo.hardware')}</div>
        <div className="hwgrid">
          {/* CPU Temp */}
          <div className="hwitem">
            <div className="hwlabel">{t('systemInfo.cpuTemp')}</div>
            <div className="hwval">{stats ? `${stats.cpuTemp.toFixed(0)}°C` : '—'}</div>
            <div className="hwbar">
              <div className="hwfill" style={{ width: `${tmpPct}%`, background: barColor(tmpPct) }} />
            </div>
          </div>
          {/* CPU Load */}
          <div className="hwitem">
            <div className="hwlabel">{t('systemInfo.cpuLoad')}</div>
            <div className="hwval">{stats ? `${stats.cpuLoad[0]} (${stats.cpuCount} ${t('systemInfo.cores')})` : '—'}</div>
            <div className="hwbar">
              <div className="hwfill" style={{ width: `${cpuLoadPct}%`, background: barColor(cpuLoadPct) }} />
            </div>
            {stats && (
              <div style={{ fontSize: 10, color: 'var(--tx3)', marginTop: 2 }}>
                {t('systemInfo.load1m')}: {stats.cpuLoad[0]} · {t('systemInfo.load5m')}: {stats.cpuLoad[1]} · {t('systemInfo.load15m')}: {stats.cpuLoad[2]}
              </div>
            )}
          </div>
          {/* RAM (merged: used / total) */}
          <div className="hwitem">
            <div className="hwlabel">{t('systemInfo.ram')}</div>
            <div className="hwval">
              {stats ? `${(stats.ramUsedMb / 1024).toFixed(1)} / ${(stats.ramTotalMb / 1024).toFixed(1)} GB` : '—'}
            </div>
            <div className="hwbar">
              <div className="hwfill" style={{ width: `${ramPct}%`, background: barColor(ramPct) }} />
            </div>
            {stats && (
              <div style={{ fontSize: 10, color: 'var(--tx3)', marginTop: 2 }}>
                {ramPct}% {t('systemInfo.used')}
              </div>
            )}
          </div>
          {/* Swap */}
          {stats && stats.swapTotalMb > 0 && (
            <div className="hwitem">
              <div className="hwlabel">{t('systemInfo.swap')}</div>
              <div className="hwval">{`${stats.swapUsedMb} / ${stats.swapTotalMb} MB`}</div>
              <div className="hwbar">
                <div className="hwfill" style={{ width: `${swapPct}%`, background: barColor(swapPct) }} />
              </div>
            </div>
          )}
          {/* Disk */}
          <div className="hwitem">
            <div className="hwlabel">{t('systemInfo.disk')}</div>
            <div className="hwval">
              {stats ? `${stats.diskUsedGb.toFixed(1)} / ${stats.diskTotalGb.toFixed(1)} GB` : '—'}
            </div>
            <div className="hwbar">
              <div className="hwfill" style={{ width: `${diskPct}%`, background: barColor(diskPct) }} />
            </div>
          </div>
        </div>
      </div>

      {/* LLM / Ollama */}
      <div className="card" style={{ marginTop: 8 }}>
        <div className="card-title">{t('systemInfo.llmEngine')}</div>
        <div className="sysrow">
          <span className="srl">{t('systemInfo.ollamaInstalled')}</span>
          <span className={`badge ${ollama?.installed ? 'bg-run' : 'bg-stop'}`}>
            {ollama?.installed ? t('common.yes') : t('systemInfo.notInstalled')}
          </span>
        </div>
        {ollama?.installed && (
          <>
            <div className="sysrow">
              <span className="srl">{t('systemInfo.ollamaRunning')}</span>
              <span className={`badge ${ollama.running ? 'bg-run' : 'bg-stop'}`}>
                {ollama.running ? t('systemInfo.running') : t('systemInfo.stopped')}
              </span>
            </div>
            <div className="sysrow">
              <span className="srl">{t('systemInfo.activeModel')}</span>
              <span className="srv">{ollama.model ?? t('systemInfo.noModel')}</span>
            </div>
            <div className="sysrow">
              <span className="srl">{t('systemInfo.modelLoaded')}</span>
              <span className={`badge ${ollama.modelLoaded ? 'bg-run' : 'bg-stop'}`}>
                {ollama.modelLoaded
                  ? (ollama.loadedModel ?? t('common.yes'))
                  : t('systemInfo.modelNotLoaded')}
              </span>
            </div>
            {ollama.models && ollama.models.length > 0 && (
              <div className="sysrow" style={{ flexDirection: 'column', alignItems: 'flex-start', gap: 2 }}>
                <span className="srl">{t('systemInfo.installedModels')}:</span>
                {ollama.models.map((m) => (
                  <span key={m.name} className="srv" style={{ fontSize: 11, marginLeft: 8 }}>
                    {m.name} ({m.size_mb} MB)
                  </span>
                ))}
              </div>
            )}
          </>
        )}
      </div>

      {/* Processes */}
      <div className="card" style={{ marginTop: 8 }}>
        <div className="card-title" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span>{t('systemInfo.processes')}</span>
          {procVisible && (
            <div style={{ display: 'flex', gap: 6 }}>
              <button
                className={`btn btn-xs ${procSort === 'cpu' ? 'btn-active' : ''}`}
                onClick={() => handleSortChange('cpu')}
              >
                {t('systemInfo.sortByCpu')}
              </button>
              <button
                className={`btn btn-xs ${procSort === 'ram' ? 'btn-active' : ''}`}
                onClick={() => handleSortChange('ram')}
              >
                {t('systemInfo.sortByRam')}
              </button>
              <button className="btn btn-xs" onClick={() => loadProcesses()}>
                {t('systemInfo.refresh')}
              </button>
            </div>
          )}
        </div>
        {!procVisible ? (
          <button className="btn btn-sm" onClick={handleShowProcesses} style={{ marginTop: 4 }}>
            {t('systemInfo.loadProcesses')}
          </button>
        ) : procLoading ? (
          <div style={{ padding: 8, color: 'var(--tx3)' }}>{t('common.loading')}</div>
        ) : (
          <div style={{ overflowX: 'auto', marginTop: 4 }}>
            <table className="proc-table">
              <thead>
                <tr>
                  <th>{t('systemInfo.pid')}</th>
                  <th>{t('systemInfo.processName')}</th>
                  <th>{t('systemInfo.user')}</th>
                  <th>{t('systemInfo.cpu')} %</th>
                  <th>MEM %</th>
                  <th>{t('systemInfo.ramMb')} MB</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {processes.map((p) => (
                  <tr key={p.pid}>
                    <td style={{ color: 'var(--tx3)' }}>{p.pid}</td>
                    <td style={{ maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {p.name}
                    </td>
                    <td style={{ color: 'var(--tx3)' }}>{p.user}</td>
                    <td style={{ color: p.cpu > 50 ? 'var(--rd)' : p.cpu > 20 ? 'var(--am)' : 'var(--tx2)' }}>
                      {p.cpu}
                    </td>
                    <td style={{ color: p.mem_pct > 20 ? 'var(--am)' : 'var(--tx2)' }}>{p.mem_pct}</td>
                    <td>{p.ram_mb.toFixed(0)}</td>
                    <td>
                      <button
                        className="btn btn-xs btn-danger"
                        onClick={() => handleKill(p)}
                        title={t('systemInfo.kill')}
                      >
                        {t('systemInfo.kill')}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
