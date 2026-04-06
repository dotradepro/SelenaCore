import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useStore, Module } from '../store/useStore';

const PAGE_SIZE = 12;

const TYPE_CLS: Record<string, string> = {
  INTEGRATION: 'bg-int',
  DRIVER: 'bg-int',
  SYSTEM: 'bg-sys',
  AUTOMATION: 'bg-int',
  UI: 'bg-int',
  IMPORT_SOURCE: 'bg-int',
};

const STATUS_CLS: Record<string, string> = {
  RUNNING: 'bg-run',
  STOPPED: 'bg-stop',
  ERROR: 'bg-stop',
};

const TYPE_EMOJI: Record<string, string> = {
  INTEGRATION: '🔗',
  DRIVER: '🔌',
  SYSTEM: '⚙️',
  AUTOMATION: '⚡',
  UI: '🖥',
  IMPORT_SOURCE: '📥',
};

/** Shows the module's icon file if available, falls back to type emoji */
function ModuleIcon({ mod }: { mod: Module }) {
  const [failed, setFailed] = useState(false);
  const hasIcon = !!mod.ui?.icon && !failed;

  if (hasIcon) {
    return (
      <img
        src={`/api/ui/modules/${mod.name}/icon`}
        alt={mod.name}
        onError={() => setFailed(true)}
        style={{
          width: 28, height: 28, objectFit: 'contain',
          borderRadius: 6, flexShrink: 0,
        }}
      />
    );
  }
  return <span style={{ fontSize: 18, lineHeight: 1 }}>{TYPE_EMOJI[mod.type] || '📦'}</span>;
}

function ModuleCard({ mod }: { mod: Module }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const stopModule = useStore((s) => s.stopModule);
  const startModule = useStore((s) => s.startModule);
  const removeModule = useStore((s) => s.removeModule);
  const showToast = useStore((s) => s.showToast);
  const [busy, setBusy] = useState(false);

  const isRunning = mod.status === 'RUNNING';
  const isSystem = mod.type === 'SYSTEM';

  async function handle(fn: () => Promise<void>, successKey: string, errorKey: string) {
    setBusy(true);
    try {
      await fn();
      showToast?.(t(successKey), 'success');
    } catch {
      showToast?.(t(errorKey), 'error');
    } finally {
      setBusy(false);
    }
  }

  const installedDate = mod.installed_at
    ? new Date(mod.installed_at * 1000).toLocaleDateString()
    : '';

  return (
    <div className="mcard">
      <div
        className="mcard-ico"
        style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
        onClick={() => navigate(`/modules/${mod.name}`)}
      >
        <ModuleIcon mod={mod} />
      </div>
      <div
        className="mcard-info"
        style={{ cursor: 'pointer' }}
        onClick={() => navigate(`/modules/${mod.name}`)}
      >
        <div className="mcard-name">{mod.name}</div>
        <div className="mcard-sub">
          v{mod.version} · {mod.type}{mod.port ? ` · :${mod.port}` : ''}
          {installedDate && ` · ${installedDate}`}
        </div>
      </div>
      <span className={`badge ${TYPE_CLS[mod.type] || 'bg-int'}`} style={{ marginRight: 4 }}>
        {mod.type}
      </span>
      <span className={`badge ${STATUS_CLS[mod.status] || 'bg-stop'}`}>
        {mod.status.toLowerCase()}
      </span>

      {/* action buttons */}
      {!isSystem && (
        <div style={{ display: 'flex', gap: 4, marginLeft: 8 }}>
          {isRunning ? (
            <button
              disabled={busy}
              onClick={() => handle(() => stopModule(mod.name), 'modules.stopped', 'modules.stopFailed')}
              title={t('modules.stop')}
              style={{
                width: 26, height: 26, borderRadius: 6,
                background: 'rgba(224,84,84,.1)',
                border: '1px solid rgba(224,84,84,.15)',
                color: 'var(--rd)', cursor: 'pointer',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                opacity: busy ? .5 : 1, transition: 'opacity .15s',
              }}
            >
              <svg viewBox="0 0 12 12" fill="none" width="11" height="11">
                <rect x="3" y="3" width="6" height="6" rx="1" fill="currentColor" />
              </svg>
            </button>
          ) : (
            <button
              disabled={busy}
              onClick={() => handle(() => startModule(mod.name), 'modules.started', 'modules.startFailed')}
              title={t('modules.start')}
              style={{
                width: 26, height: 26, borderRadius: 6,
                background: 'rgba(46,201,138,.1)',
                border: '1px solid rgba(46,201,138,.15)',
                color: 'var(--gr)', cursor: 'pointer',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                opacity: busy ? .5 : 1, transition: 'opacity .15s',
              }}
            >
              <svg viewBox="0 0 12 12" fill="none" width="11" height="11">
                <path d="M3.5 2.5l6 3.5-6 3.5V2.5Z" fill="currentColor" />
              </svg>
            </button>
          )}
          <button
            disabled={busy}
            onClick={() => handle(() => removeModule(mod.name), 'modules.removed', 'modules.removeFailed')}
            title={t('modules.remove')}
            style={{
              width: 26, height: 26, borderRadius: 6,
              background: 'rgba(255,255,255,.04)',
              border: '1px solid var(--b)',
              color: 'var(--tx3)', cursor: 'pointer',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              opacity: busy ? .5 : 1, transition: 'opacity .15s',
            }}
          >
            <svg viewBox="0 0 12 12" fill="none" width="11" height="11">
              <path d="M2 3h8M5 3V2h2v1M4.5 5l.5 5M7.5 5-.5 5M3 3l.5 7h5L9 3" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round" />
            </svg>
          </button>
        </div>
      )}
    </div>
  );
}

// ── Filter bar ──────────────────────────────────────────────────────────────

const ALL_TYPES = ['SYSTEM', 'UI', 'INTEGRATION', 'DRIVER', 'AUTOMATION', 'IMPORT_SOURCE'];

function FilterBar({
  search, onSearch,
  status, onStatus,
  typeFilter, onType,
  types,
}: {
  search: string; onSearch: (v: string) => void;
  status: string; onStatus: (v: string) => void;
  typeFilter: string; onType: (v: string) => void;
  types: string[];
}) {
  const { t } = useTranslation();

  const statusOpts = [
    { key: 'all', label: t('modules.filterAll') },
    { key: 'RUNNING', label: t('modules.filterRunning') },
    { key: 'STOPPED', label: t('modules.filterStopped') },
    { key: 'ERROR', label: t('modules.filterError') },
  ];

  const chipStyle = (active: boolean): React.CSSProperties => ({
    padding: '3px 9px', borderRadius: 20, fontSize: 10, fontWeight: 500,
    cursor: 'pointer', border: 'none', transition: 'all .15s',
    background: active ? 'var(--ac)' : 'var(--sf3)',
    color: active ? '#fff' : 'var(--tx2)',
  });

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 10 }}>
      {/* Search */}
      <input
        type="search"
        placeholder={t('modules.searchPlaceholder')}
        value={search}
        onChange={(e) => { onSearch(e.target.value); }}
        style={{
          width: '100%', padding: '7px 10px', borderRadius: 8, fontSize: 11,
          background: 'var(--sf)', border: '1px solid var(--b)',
          color: 'var(--tx)', outline: 'none',
        }}
      />

      {/* Status tabs */}
      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
        {statusOpts.map((o) => (
          <button key={o.key} style={chipStyle(status === o.key)} onClick={() => onStatus(o.key)}>
            {o.label}
          </button>
        ))}
      </div>

      {/* Type chips — only show types that exist */}
      {types.length > 1 && (
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', alignItems: 'center' }}>
          <span style={{ fontSize: 10, color: 'var(--tx3)', marginRight: 2 }}>
            {t('modules.filterByType')}:
          </span>
          <button style={chipStyle(typeFilter === 'all')} onClick={() => onType('all')}>
            {t('modules.filterAll')}
          </button>
          {types.map((tp) => (
            <button key={tp} style={chipStyle(typeFilter === tp)} onClick={() => onType(tp)}>
              {TYPE_EMOJI[tp] || ''} {tp}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Pagination ──────────────────────────────────────────────────────────────

function Pagination({ page, total, onPage }: { page: number; total: number; onPage: (n: number) => void }) {
  const { t } = useTranslation();
  if (total <= 1) return null;
  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      gap: 10, marginTop: 10,
    }}>
      <button
        disabled={page === 1}
        onClick={() => onPage(page - 1)}
        style={{
          padding: '4px 12px', borderRadius: 6, fontSize: 11,
          background: 'var(--sf2)', border: '1px solid var(--b)',
          color: page === 1 ? 'var(--tx3)' : 'var(--tx2)', cursor: page === 1 ? 'default' : 'pointer',
        }}
      >
        {t('modules.prev')}
      </button>
      <span style={{ fontSize: 11, color: 'var(--tx3)' }}>
        {page} {t('modules.pageOf')} {total}
      </span>
      <button
        disabled={page === total}
        onClick={() => onPage(page + 1)}
        style={{
          padding: '4px 12px', borderRadius: 6, fontSize: 11,
          background: 'var(--sf2)', border: '1px solid var(--b)',
          color: page === total ? 'var(--tx3)' : 'var(--tx2)', cursor: page === total ? 'default' : 'pointer',
        }}
      >
        {t('modules.next')}
      </button>
    </div>
  );
}

// ── Main page ───────────────────────────────────────────────────────────────

export default function Modules() {
  const { t } = useTranslation();
  const modules = useStore((s) => s.modules);
  const modulesLoading = useStore((s) => s.modulesLoading);
  const fetchModules = useStore((s) => s.fetchModules);
  const showToast = useStore((s) => s.showToast);
  const fileRef = useRef<HTMLInputElement>(null);
  const [installing, setInstalling] = useState(false);
  const [installMsg, setInstallMsg] = useState('');

  // Filters
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [typeFilter, setTypeFilter] = useState('all');
  const [page, setPage] = useState(1);

  useEffect(() => { fetchModules(); }, [fetchModules]);

  // Reset to page 1 whenever filters change
  useEffect(() => { setPage(1); }, [search, statusFilter, typeFilter]);

  async function handleInstall(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setInstalling(true);
    setInstallMsg(t('modules.uploading'));
    const form = new FormData();
    form.append('module', file);
    try {
      const res = await fetch('/api/ui/modules/install', { method: 'POST', body: form });
      if (res.ok) {
        setInstallMsg(t('modules.uploadedValidating'));
        showToast?.(t('modules.installed'), 'success');
        setTimeout(() => { fetchModules(); setInstallMsg(''); }, 3000);
      } else {
        setInstallMsg('');
        showToast?.(t('modules.installFailed'), 'error');
      }
    } catch {
      setInstallMsg('');
      showToast?.(t('modules.networkError'), 'error');
    } finally {
      setInstalling(false);
      e.target.value = '';
    }
  }

  // SYSTEM modules are managed on a separate /settings/system-modules page,
  // so this page only ever lists user modules.
  const userModules = modules.filter((m) => m.type !== 'SYSTEM');

  // Collect types that actually exist in the user-modules list
  const presentTypes = ALL_TYPES.filter(
    (tp) => tp !== 'SYSTEM' && userModules.some((m) => m.type === tp),
  );

  // Apply filters
  const filtered = userModules.filter((m) => {
    if (search && !m.name.toLowerCase().includes(search.toLowerCase())) return false;
    if (statusFilter !== 'all' && m.status !== statusFilter) return false;
    if (typeFilter !== 'all' && m.type !== typeFilter) return false;
    return true;
  });

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const pageMods = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  const running = userModules.filter((m) => m.status === 'RUNNING').length;

  return (
    <div className="generic-page">
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--tx)' }}>
            {t('modules.title')}
          </div>
          <div style={{ fontSize: 10, color: 'var(--tx3)', marginTop: 1 }}>
            {t('modules.total', { running, total: userModules.length })}
          </div>
        </div>
        <button
          onClick={() => fetchModules()}
          disabled={modulesLoading}
          style={{
            padding: '5px 10px', borderRadius: 6,
            background: 'var(--sf2)', border: '1px solid var(--b)',
            color: 'var(--tx2)', fontSize: 11, cursor: modulesLoading ? 'default' : 'pointer',
            opacity: modulesLoading ? 0.6 : 1, transition: 'opacity .15s',
            display: 'flex', alignItems: 'center', gap: 4,
          }}
        >
          <span style={{
            display: 'inline-block',
            animation: modulesLoading ? 'spin .8s linear infinite' : 'none',
          }}>↺</span>
          {t('modules.refresh')}
        </button>
      </div>

      {/* Filter bar — only show when there are modules */}
      {userModules.length > 0 && (
        <FilterBar
          search={search} onSearch={setSearch}
          status={statusFilter} onStatus={setStatusFilter}
          typeFilter={typeFilter} onType={setTypeFilter}
          types={presentTypes}
        />
      )}

      {/* Module list */}
      {userModules.length === 0 ? (
        <div style={{
          textAlign: 'center', padding: '40px 20px',
          color: 'var(--tx3)', fontSize: 11,
          background: 'var(--sf)', borderRadius: 'var(--r)',
          border: '1px solid var(--b)',
        }}>
          {t('modules.noModulesInstalled')}
        </div>
      ) : filtered.length === 0 ? (
        <div style={{
          textAlign: 'center', padding: '30px 20px',
          color: 'var(--tx3)', fontSize: 11,
          background: 'var(--sf)', borderRadius: 'var(--r)',
          border: '1px solid var(--b)',
        }}>
          {t('modules.noResults')}
        </div>
      ) : (
        pageMods.map((mod) => <ModuleCard key={mod.name} mod={mod} />)
      )}

      {/* Pagination */}
      <Pagination page={page} total={totalPages} onPage={setPage} />

      {/* Install from ZIP */}
      <div
        onClick={() => !installing && fileRef.current?.click()}
        style={{
          border: '1.5px dashed var(--b2)',
          borderRadius: 'var(--r)',
          padding: '18px',
          textAlign: 'center',
          color: installMsg
            ? (installMsg.startsWith('✓') ? 'var(--gr)' : installMsg.startsWith('✗') ? 'var(--rd)' : 'var(--tx2)')
            : 'var(--tx3)',
          fontSize: 11,
          marginTop: 8,
          cursor: installing ? 'default' : 'pointer',
          transition: 'border-color .15s',
        }}
      >
        {installMsg || t('modules.installFromZip')}
      </div>
      <input
        ref={fileRef}
        type="file"
        accept=".zip"
        style={{ display: 'none' }}
        onChange={handleInstall}
      />
    </div>
  );
}
