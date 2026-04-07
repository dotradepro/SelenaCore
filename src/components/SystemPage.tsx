import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router-dom';
import { Info, X } from 'lucide-react';
import { useStore } from '../store/useStore';
import IntegrityPage from './IntegrityPage';

interface ProcessInfo {
  pid: number;
  name: string;
  user: string;
  cpu: number;
  mem_pct: number;
  ram_mb: number;
  status: string;
}

type TabId = 'overview' | 'processes' | 'integrity';

function fmtUptime(sec: number): string {
  if (!sec) return '—';
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  return `${h}h ${m}m`;
}

function barColor(pct: number): string {
  if (pct < 60) return 'var(--gr)';
  if (pct < 80) return 'var(--am)';
  return 'var(--rd)';
}

// ────────────────────────────────────────────────────────────────────────────
//  Reusable bits
// ────────────────────────────────────────────────────────────────────────────

interface StatTileProps {
  label: string;
  value: ReactNode;
  accent?: 'gr' | 'am' | 'rd' | 'ac' | 'tx';
  onInfo?: () => void;
  bar?: { pct: number; color?: string };
  sub?: ReactNode;
}

function StatTile({ label, value, accent = 'tx', onInfo, bar, sub }: StatTileProps) {
  const colorMap: Record<string, string> = {
    gr: 'var(--gr)', am: 'var(--am)', rd: 'var(--rd)', ac: 'var(--ac)', tx: 'var(--tx)',
  };
  return (
    <div className="hwitem" style={{ position: 'relative' }}>
      <div className="hwlabel" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 4 }}>
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{label}</span>
        {onInfo && (
          <button
            type="button"
            onClick={onInfo}
            style={{
              background: 'none', border: 'none', padding: 0, cursor: 'pointer',
              color: 'var(--tx3)', display: 'flex', alignItems: 'center',
            }}
            title="Details"
          >
            <Info size={11} />
          </button>
        )}
      </div>
      <div className="hwval" style={{ color: colorMap[accent] }}>{value}</div>
      {bar && (
        <div className="hwbar">
          <div className="hwfill" style={{ width: `${bar.pct}%`, background: bar.color ?? barColor(bar.pct) }} />
        </div>
      )}
      {sub && <div style={{ fontSize: 10, color: 'var(--tx3)', marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

interface DetailModalProps {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
}

function DetailModal({ open, title, onClose, children }: DetailModalProps) {
  if (!open) return null;
  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 1000,
        background: 'rgba(0,0,0,.55)', backdropFilter: 'blur(3px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: 'var(--sf)', border: '1px solid var(--b)', borderRadius: 12,
          minWidth: 320, maxWidth: 560, maxHeight: '80vh', overflow: 'auto',
          boxShadow: '0 8px 32px rgba(0,0,0,.5)',
        }}
      >
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '12px 16px', borderBottom: '1px solid var(--b)',
        }}>
          <span style={{ fontSize: 13, fontWeight: 600 }}>{title}</span>
          <button
            type="button"
            onClick={onClose}
            style={{ background: 'none', border: 'none', padding: 4, cursor: 'pointer', color: 'var(--tx2)' }}
          >
            <X size={16} />
          </button>
        </div>
        <div style={{ padding: 16 }}>{children}</div>
      </div>
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────────
//  System Page
// ────────────────────────────────────────────────────────────────────────────

export default function SystemPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { tab: urlTab } = useParams<{ tab?: string }>();
  const stats = useStore((s) => s.stats);
  const health = useStore((s) => s.health);
  const fetchStats = useStore((s) => s.fetchStats);

  const activeTab: TabId =
    urlTab === 'processes' || urlTab === 'integrity' ? urlTab : 'overview';

  const [modal, setModal] = useState<null | 'core' | 'llm' | 'cloud' | 'native' | 'ollama'>(null);

  useEffect(() => {
    fetchStats();
    const id = setInterval(fetchStats, 15000);
    return () => clearInterval(id);
  }, [fetchStats]);

  const setTab = (id: TabId) => {
    navigate(id === 'overview' ? '/settings/system-info' : `/settings/system-info/${id}`);
  };

  const tabs: { id: TabId; label: string }[] = useMemo(() => [
    { id: 'overview', label: t('systemInfo.tabOverview') },
    { id: 'processes', label: t('systemInfo.tabProcesses') },
    { id: 'integrity', label: t('systemInfo.tabIntegrity') },
  ], [t]);

  return (
    <div className="generic-page" style={{ paddingTop: 0 }}>
      {/* Sub-tabs */}
      <div style={{
        display: 'flex', gap: 4, borderBottom: '1px solid var(--b)',
        marginBottom: 12, position: 'sticky', top: 0, background: 'var(--bg)', zIndex: 5,
      }}>
        {tabs.map((tb) => {
          const isActive = tb.id === activeTab;
          return (
            <button
              key={tb.id}
              type="button"
              onClick={() => setTab(tb.id)}
              style={{
                background: 'none', border: 'none', cursor: 'pointer',
                padding: '8px 14px', fontSize: 12,
                fontWeight: isActive ? 600 : 400,
                color: isActive ? 'var(--tx)' : 'var(--tx3)',
                borderBottom: `2px solid ${isActive ? 'var(--ac)' : 'transparent'}`,
              }}
            >
              {tb.label}
            </button>
          );
        })}
      </div>

      {activeTab === 'overview' && (
        <OverviewTab
          stats={stats}
          health={health}
          openModal={(m) => setModal(m)}
        />
      )}
      {activeTab === 'processes' && <ProcessesTab />}
      {activeTab === 'integrity' && <IntegrityPage />}

      {/* Detail modals */}
      <DetailModal open={modal === 'core'} title={t('systemInfo.coreDetails')} onClose={() => setModal(null)}>
        <DetailRows rows={[
          [t('systemInfo.version'), health?.version ?? stats?.version ?? '—'],
          [t('systemInfo.port'), `:${stats?.corePort ?? 80}`],
          [t('systemInfo.uptime'), fmtUptime(stats?.uptime ?? 0)],
          [t('systemInfo.mode'), stats?.mode ?? 'normal'],
          [t('systemInfo.platform'), health?.status === 'ok' ? t('systemInfo.connected') : t('systemInfo.disconnected')],
          [t('systemInfo.integrity'), stats?.integrity ?? 'ok'],
          [t('systemInfo.mdns'), 'smarthome.local'],
        ]} />
      </DetailModal>

      <DetailModal open={modal === 'llm'} title={t('systemInfo.llmDetails')} onClose={() => setModal(null)}>
        {stats?.llmEngine ? (
          <>
            <DetailRows rows={[
              [t('llmEngine.activeProvider'), stats.llmEngine.provider],
              [t('llmEngine.activeModel'), stats.llmEngine.model || '—'],
              [t('llmEngine.twoStep'), stats.llmEngine.twoStep ? t('llmEngine.enabled') : t('llmEngine.disabled')],
              [t('llmEngine.intentCache'),
                t('llmEngine.intentCacheValue', { size: stats.llmEngine.intentCache.size, hot: stats.llmEngine.intentCache.hot })],
            ]} />
            <div style={{ marginTop: 12, fontSize: 11, color: 'var(--tx3)' }}>
              {t('llmEngine.cloudProvidersHint')}
            </div>
          </>
        ) : <div style={{ color: 'var(--tx3)' }}>—</div>}
      </DetailModal>

      <DetailModal open={modal === 'cloud'} title={t('llmEngine.cloudProviders')} onClose={() => setModal(null)}>
        <div style={{ fontSize: 11, color: 'var(--tx3)', marginBottom: 8 }}>
          {t('llmEngine.cloudProvidersHint')}
        </div>
        {(stats?.llmEngine?.cloudProviders ?? []).map((p) => {
          let label: string;
          let cls: string;
          if (p.active) { label = t('llmEngine.inUse'); cls = 'bg-run'; }
          else if (p.configured) { label = t('llmEngine.keySaved'); cls = 'bg-int'; }
          else { label = t('llmEngine.notConfigured'); cls = 'bg-stop'; }
          return (
            <div key={p.id} style={{
              display: 'flex', alignItems: 'center', gap: 8,
              padding: '6px 0', borderTop: '1px solid var(--b)',
            }}>
              <span style={{ minWidth: 90, fontSize: 12, fontWeight: 500 }}>{p.name}</span>
              <span className={`badge ${cls}`}>{label}</span>
              {p.configured && p.model && (
                <span style={{ color: 'var(--tx3)', fontSize: 11, marginLeft: 'auto' }}>{p.model}</span>
              )}
            </div>
          );
        })}
      </DetailModal>

      <DetailModal open={modal === 'ollama'} title="Ollama" onClose={() => setModal(null)}>
        {stats?.ollama ? (
          <>
            <DetailRows rows={[
              [t('systemInfo.ollamaInstalled'), stats.ollama.installed ? t('common.yes') : t('systemInfo.notInstalled')],
              [t('systemInfo.ollamaRunning'), stats.ollama.running ? t('systemInfo.running') : t('systemInfo.stopped')],
              [t('systemInfo.activeModel'), stats.ollama.model ?? t('systemInfo.noModel')],
              [t('systemInfo.modelLoaded'), stats.ollama.modelLoaded
                ? (stats.ollama.loadedModel ?? t('common.yes'))
                : t('systemInfo.modelNotLoaded')],
            ]} />
            {stats.ollama.models && stats.ollama.models.length > 0 && (
              <div style={{ marginTop: 12 }}>
                <div style={{ fontSize: 11, color: 'var(--tx3)', marginBottom: 4 }}>
                  {t('systemInfo.installedModels')}
                </div>
                {stats.ollama.models.map((m) => (
                  <div key={m.name} style={{ fontSize: 11, padding: '2px 0' }}>
                    {m.name} <span style={{ color: 'var(--tx3)' }}>· {m.size_mb} MB</span>
                  </div>
                ))}
              </div>
            )}
          </>
        ) : <div style={{ color: 'var(--tx3)' }}>—</div>}
      </DetailModal>

      <DetailModal open={modal === 'native'} title={t('nativeServices.title')} onClose={() => setModal(null)}>
        {(stats?.nativeServices ?? []).map((svc) => (
          <div key={svc.name} style={{
            display: 'flex', alignItems: 'center', gap: 8,
            padding: '6px 0', borderTop: '1px solid var(--b)',
          }}>
            <span
              style={{
                width: 8, height: 8, borderRadius: '50%',
                background: svc.running ? 'var(--gr)' : 'var(--rd)', flexShrink: 0,
              }}
            />
            <span style={{ minWidth: 90, fontSize: 12, fontWeight: 500 }}>
              {t(`nativeServices.${svc.name}`)}
            </span>
            {svc.url && (
              <span style={{ fontSize: 10, color: 'var(--tx3)' }}>{svc.url}</span>
            )}
            <span style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--tx3)' }}>
              {Object.entries(svc.extra ?? {})
                .filter(([, v]) => v !== null && v !== undefined && v !== '')
                .map(([k, v]) => `${k}=${v}`)
                .join(' · ')}
            </span>
          </div>
        ))}
      </DetailModal>
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────────
//  Overview tab
// ────────────────────────────────────────────────────────────────────────────

interface OverviewTabProps {
  stats: ReturnType<typeof useStore.getState>['stats'];
  health: ReturnType<typeof useStore.getState>['health'];
  openModal: (m: 'core' | 'llm' | 'cloud' | 'native' | 'ollama') => void;
}

function OverviewTab({ stats, health, openModal }: OverviewTabProps) {
  const { t } = useTranslation();

  const ramPct = stats ? Math.round((stats.ramUsedMb / stats.ramTotalMb) * 100) : 0;
  const swapPct = stats && stats.swapTotalMb > 0 ? Math.round((stats.swapUsedMb / stats.swapTotalMb) * 100) : 0;
  const diskPct = stats ? Math.round((stats.diskUsedGb / stats.diskTotalGb) * 100) : 0;
  const tmpPct = stats ? Math.min(100, Math.round((stats.cpuTemp / 90) * 100)) : 0;
  const cpuLoadPct = stats ? Math.min(100, Math.round((stats.cpuLoad[0] / stats.cpuCount) * 100)) : 0;

  const llmEngine = stats?.llmEngine;
  const nativeServices = stats?.nativeServices ?? [];
  const cloudConfigured = (llmEngine?.cloudProviders ?? []).filter((p) => p.configured).length;

  const platformOk = health?.status === 'ok';
  const integrityOk = (stats?.integrity ?? 'ok') === 'ok';

  return (
    <>
      {/* Status block — Core + Network + LLM merged into compact tiles */}
      <div className="card">
        <div className="card-title">{t('systemInfo.systemStatus')}</div>
        <div className="hwgrid">
          <StatTile
            label={t('systemInfo.version')}
            value={<span style={{ fontSize: 14 }}>{health?.version ?? stats?.version ?? '—'}</span>}
            onInfo={() => openModal('core')}
          />
          <StatTile
            label={t('systemInfo.port')}
            value={<span style={{ fontSize: 18 }}>:{stats?.corePort ?? 80}</span>}
          />
          <StatTile
            label={t('systemInfo.uptime')}
            value={<span style={{ fontSize: 16 }}>{fmtUptime(stats?.uptime ?? 0)}</span>}
          />
          <StatTile
            label={t('systemInfo.mode')}
            value={<span style={{ fontSize: 14, color: stats?.mode === 'safe_mode' ? 'var(--am)' : 'var(--gr)' }}>
              {stats?.mode ?? 'normal'}
            </span>}
          />
          <StatTile
            label={t('systemInfo.platform')}
            value={<span className={`badge ${platformOk ? 'bg-run' : 'bg-stop'}`}>
              {platformOk ? t('systemInfo.online') : t('systemInfo.offline')}
            </span>}
          />
          <StatTile
            label={t('systemInfo.integrity')}
            value={<span className={`badge ${integrityOk ? 'bg-run' : 'bg-stop'}`}>
              {stats?.integrity ?? 'ok'}
            </span>}
          />
          <StatTile
            label={t('llmEngine.activeProvider')}
            value={
              <span style={{ fontSize: 13, textTransform: 'capitalize' }}>
                {llmEngine?.provider ?? '—'}
              </span>
            }
            sub={llmEngine?.model || ''}
            onInfo={() => openModal('llm')}
          />
          <StatTile
            label={t('llmEngine.cloudProviders')}
            value={<span style={{ fontSize: 16 }}>{cloudConfigured}/{llmEngine?.cloudProviders.length ?? 0}</span>}
            sub={t('llmEngine.keySavedCount', { count: cloudConfigured })}
            onInfo={() => openModal('cloud')}
          />
          <StatTile
            label={t('llmEngine.intentCache')}
            value={<span style={{ fontSize: 16 }}>{llmEngine?.intentCache.size ?? 0}</span>}
            sub={t('llmEngine.hotCount', { count: llmEngine?.intentCache.hot ?? 0 })}
          />
        </div>
      </div>

      {/* Hardware grid */}
      <div className="card" style={{ marginTop: 8 }}>
        <div className="card-title">{t('systemInfo.hardware')}</div>
        <div className="hwgrid">
          <StatTile
            label={t('systemInfo.cpuTemp')}
            value={stats ? `${stats.cpuTemp.toFixed(0)}°C` : '—'}
            bar={{ pct: tmpPct }}
          />
          <StatTile
            label={t('systemInfo.cpuLoad')}
            value={stats ? `${stats.cpuLoad[0]}` : '—'}
            sub={stats ? `${stats.cpuCount} ${t('systemInfo.cores')} · ${stats.cpuLoad[1]} · ${stats.cpuLoad[2]}` : ''}
            bar={{ pct: cpuLoadPct }}
          />
          <StatTile
            label={t('systemInfo.ram')}
            value={stats ? `${(stats.ramUsedMb / 1024).toFixed(1)}/${(stats.ramTotalMb / 1024).toFixed(1)} GB` : '—'}
            bar={{ pct: ramPct }}
            sub={stats ? `${ramPct}% ${t('systemInfo.used')}` : ''}
          />
          {stats && stats.swapTotalMb > 0 && (
            <StatTile
              label={t('systemInfo.swap')}
              value={`${stats.swapUsedMb}/${stats.swapTotalMb} MB`}
              bar={{ pct: swapPct }}
            />
          )}
          <StatTile
            label={t('systemInfo.disk')}
            value={stats ? `${stats.diskUsedGb.toFixed(1)}/${stats.diskTotalGb.toFixed(1)} GB` : '—'}
            bar={{ pct: diskPct }}
          />
        </div>
      </div>

      {/* Native services */}
      <div className="card" style={{ marginTop: 8 }}>
        <div className="card-title" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span>{t('nativeServices.title')}</span>
          <button
            type="button"
            onClick={() => openModal('native')}
            style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer', color: 'var(--tx3)' }}
            title="Details"
          >
            <Info size={13} />
          </button>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))', gap: 6 }}>
          {nativeServices.map((svc) => {
            const extra = svc.extra ?? {};
            let detail = '';
            if (svc.name === 'ollama') {
              const c = extra.models_count as number | undefined;
              if (c !== undefined) detail = `${c} models`;
            } else if (svc.name === 'piper') {
              const dev = extra.device as string | undefined;
              if (dev) detail = dev;
            } else if (svc.name === 'alsa') {
              const c = extra.cards as number | undefined;
              if (c !== undefined) detail = `${c} ${t('nativeServices.cards')}`;
            } else if (svc.name === 'vosk') {
              const c = extra.models as number | undefined;
              if (c !== undefined) detail = `${c} ${t('nativeServices.models')}`;
            }
            const isOllama = svc.name === 'ollama';
            return (
              <button
                key={svc.name}
                type="button"
                onClick={isOllama ? () => openModal('ollama') : undefined}
                style={{
                  border: '1px solid var(--b)',
                  borderRadius: 6,
                  padding: '6px 8px',
                  background: 'var(--sf2)',
                  cursor: isOllama ? 'pointer' : 'default',
                  textAlign: 'left',
                  font: 'inherit',
                  color: 'inherit',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 6 }}>
                  <span style={{ fontSize: 12, fontWeight: 600 }}>
                    {t(`nativeServices.${svc.name}`)}
                  </span>
                  <span
                    title={svc.running ? t('systemInfo.running') : t('systemInfo.stopped')}
                    style={{
                      width: 8, height: 8, borderRadius: '50%',
                      background: svc.running ? 'var(--gr)' : 'var(--rd)', flexShrink: 0,
                    }}
                  />
                </div>
                {detail && (
                  <div style={{ fontSize: 10, color: 'var(--tx3)', marginTop: 2 }}>{detail}</div>
                )}
              </button>
            );
          })}
          {nativeServices.length === 0 && (
            <div style={{ fontSize: 11, color: 'var(--tx3)' }}>—</div>
          )}
        </div>
      </div>
    </>
  );
}

// ────────────────────────────────────────────────────────────────────────────
//  Processes tab
// ────────────────────────────────────────────────────────────────────────────

function ProcessesTab() {
  const { t } = useTranslation();
  const showToast = useStore((s) => s.showToast);

  const [processes, setProcesses] = useState<ProcessInfo[]>([]);
  const [procSort, setProcSort] = useState<'cpu' | 'ram'>('cpu');
  const [procLoading, setProcLoading] = useState(false);

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

  useEffect(() => {
    loadProcesses();
    const id = setInterval(() => loadProcesses(), 5000);
    return () => clearInterval(id);
  }, [loadProcesses]);

  const handleSortChange = useCallback((sort: 'cpu' | 'ram') => {
    setProcSort(sort);
    loadProcesses(sort);
  }, [loadProcesses]);

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

  return (
    <div className="card">
      <div className="card-title" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span>{t('systemInfo.processes')}</span>
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
      </div>
      {procLoading && processes.length === 0 ? (
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
                  <td style={{ maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
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
  );
}

// ────────────────────────────────────────────────────────────────────────────
//  Helpers
// ────────────────────────────────────────────────────────────────────────────

function DetailRows({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <div>
      {rows.map(([k, v], i) => (
        <div key={i} style={{
          display: 'flex', justifyContent: 'space-between',
          padding: '6px 0', borderTop: i === 0 ? 'none' : '1px solid var(--b)',
          fontSize: 12,
        }}>
          <span style={{ color: 'var(--tx3)' }}>{k}</span>
          <span style={{ color: 'var(--tx)' }}>{v}</span>
        </div>
      ))}
    </div>
  );
}
