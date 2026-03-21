import { useEffect } from 'react';
import { useStore } from '../store/useStore';

function fmtUptime(sec: number): string {
  if (!sec) return '—';
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return `${h}h ${m}m`;
}

export default function SystemPage() {
  const stats      = useStore((s) => s.stats);
  const health     = useStore((s) => s.health);
  const fetchStats = useStore((s) => s.fetchStats);

  useEffect(() => {
    fetchStats();
    const id = setInterval(fetchStats, 15000);
    return () => clearInterval(id);
  }, [fetchStats]);

  const cpuPct  = stats ? Math.round(stats.cpuTemp) : 0;
  const ramPct  = stats ? Math.round((stats.ramUsedMb / stats.ramTotalMb) * 100) : 0;
  const diskPct = stats ? Math.round((stats.diskUsedGb / stats.diskTotalGb) * 100) : 0;
  const tmpPct  = stats ? Math.min(100, Math.round((stats.cpuTemp / 90) * 100)) : 0;

  function barColor(pct: number): string {
    if (pct < 60) return 'var(--gr)';
    if (pct < 80) return 'var(--am)';
    return 'var(--rd)';
  }

  return (
    <div className="generic-page">
      {/* Core + Network */}
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:8 }}>
        <div className="card">
          <div className="card-title">Core</div>
          <div className="sysrow">
            <span className="srl">Version</span>
            <span className="srv">{health?.version ?? stats?.version ?? '—'}</span>
          </div>
          <div className="sysrow">
            <span className="srl">API</span>
            <span className="srv">:7070</span>
          </div>
          <div className="sysrow">
            <span className="srl">UI</span>
            <span className="srv">:8080</span>
          </div>
          <div className="sysrow">
            <span className="srl">Uptime</span>
            <span className="srv">{fmtUptime(stats?.uptime ?? 0)}</span>
          </div>
          <div className="sysrow">
            <span className="srl">Mode</span>
            <span className="srv" style={{ color: stats?.mode === 'safe_mode' ? 'var(--am)' : 'var(--tx2)' }}>
              {stats?.mode ?? 'normal'}
            </span>
          </div>
          <div className="sysrow">
            <span className="srl">Platform</span>
            <span className={`badge ${health?.status === 'ok' ? 'bg-run' : 'bg-stop'}`}>
              {health?.status === 'ok' ? 'Connected' : 'Disconnected'}
            </span>
          </div>
        </div>

        <div className="card">
          <div className="card-title">Network</div>
          <div className="sysrow">
            <span className="srl">Status</span>
            <span className={`badge ${health?.status === 'ok' ? 'bg-run' : 'bg-stop'}`}>
              {health?.status === 'ok' ? 'Online' : 'Offline'}
            </span>
          </div>
          <div className="sysrow">
            <span className="srl">mDNS</span>
            <span className="srv" style={{ color:'var(--ac)' }}>smarthome.local</span>
          </div>
          <div className="sysrow">
            <span className="srl">API port</span>
            <span className="srv">7070</span>
          </div>
          <div className="sysrow">
            <span className="srl">UI port</span>
            <span className="srv">8080</span>
          </div>
          <div className="sysrow">
            <span className="srl">Integrity</span>
            <span className={`badge ${(stats?.integrity ?? 'ok') === 'ok' ? 'bg-run' : 'bg-stop'}`}>
              {stats?.integrity ?? 'ok'}
            </span>
          </div>
        </div>
      </div>

      {/* Hardware */}
      <div className="card" style={{ marginTop:8 }}>
        <div className="card-title">Hardware</div>
        <div className="hwgrid">
          <div className="hwitem">
            <div className="hwlabel">CPU Temp</div>
            <div className="hwval">{stats ? `${stats.cpuTemp.toFixed(0)}°` : '—'}</div>
            <div className="hwbar">
              <div className="hwfill" style={{ width:`${tmpPct}%`, background: barColor(tmpPct) }} />
            </div>
          </div>
          <div className="hwitem">
            <div className="hwlabel">RAM</div>
            <div className="hwval">{stats ? `${(stats.ramUsedMb / 1024).toFixed(1)} GB` : '—'}</div>
            <div className="hwbar">
              <div className="hwfill" style={{ width:`${ramPct}%`, background: barColor(ramPct) }} />
            </div>
          </div>
          <div className="hwitem">
            <div className="hwlabel">Disk</div>
            <div className="hwval">{stats ? `${stats.diskUsedGb.toFixed(1)} G` : '—'}</div>
            <div className="hwbar">
              <div className="hwfill" style={{ width:`${diskPct}%`, background: barColor(diskPct) }} />
            </div>
          </div>
          <div className="hwitem">
            <div className="hwlabel">RAM total</div>
            <div className="hwval">{stats ? `${(stats.ramTotalMb / 1024).toFixed(0)} GB` : '—'}</div>
            <div className="hwbar">
              <div className="hwfill" style={{ width:'100%', background:'var(--sf3)' }} />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
