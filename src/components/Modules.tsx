import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useStore, Module } from '../store/useStore';

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

function typeEmoji(type: string): string {
  const map: Record<string, string> = {
    INTEGRATION: '🔗',
    DRIVER: '🔌',
    SYSTEM: '⚙️',
    AUTOMATION: '⚡',
    UI: '🖥',
    IMPORT_SOURCE: '📥',
  };
  return map[type] || '📦';
}

function ModuleCard({ mod }: { mod: Module }) {
  const navigate = useNavigate();
  const stopModule = useStore((s) => s.stopModule);
  const startModule = useStore((s) => s.startModule);
  const removeModule = useStore((s) => s.removeModule);
  const [busy, setBusy] = useState(false);

  const isRunning = mod.status === 'RUNNING';
  const isSystem = mod.type === 'SYSTEM';

  async function handle(fn: () => Promise<void>) {
    setBusy(true);
    try { await fn(); } finally { setBusy(false); }
  }

  const uptime = mod.installed_at
    ? `installed ${new Date(mod.installed_at * 1000).toLocaleDateString()}`
    : '';

  return (
    <div className="mcard">
      <div
        className="mcard-ico"
        style={{ cursor: 'pointer' }}
        onClick={() => navigate(`/modules/${mod.name}`)}
      >
        {typeEmoji(mod.type)}
      </div>
      <div
        className="mcard-info"
        style={{ cursor: 'pointer' }}
        onClick={() => navigate(`/modules/${mod.name}`)}
      >
        <div className="mcard-name">{mod.name}</div>
        <div className="mcard-sub">
          v{mod.version} · {mod.type} · :{mod.port}
          {uptime && ` · ${uptime}`}
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
              onClick={() => handle(() => stopModule(mod.name))}
              title="Stop"
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
              onClick={() => handle(() => startModule(mod.name))}
              title="Start"
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
            onClick={() => handle(() => removeModule(mod.name))}
            title="Remove"
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

export default function Modules() {
  const modules = useStore((s) => s.modules);
  const fetchModules = useStore((s) => s.fetchModules);
  const fileRef = useRef<HTMLInputElement>(null);
  const [installing, setInstalling] = useState(false);
  const [installMsg, setInstallMsg] = useState('');

  useEffect(() => { fetchModules(); }, [fetchModules]);

  async function handleInstall(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setInstalling(true);
    setInstallMsg('Uploading…');
    const form = new FormData();
    form.append('module', file);
    try {
      const res = await fetch('/api/ui/modules/install', { method: 'POST', body: form });
      if (res.ok) {
        setInstallMsg('✓ Module uploaded — validating…');
        setTimeout(() => { fetchModules(); setInstallMsg(''); }, 3000);
      } else {
        setInstallMsg('✗ Upload failed');
      }
    } catch {
      setInstallMsg('✗ Network error');
    } finally {
      setInstalling(false);
      e.target.value = '';
    }
  }

  const running = modules.filter((m) => m.status === 'RUNNING').length;

  return (
    <div className="generic-page">
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--tx)' }}>Modules</div>
          <div style={{ fontSize: 10, color: 'var(--tx3)', marginTop: 1 }}>
            {running} running · {modules.length} total
          </div>
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          <button
            onClick={() => fetchModules()}
            style={{
              padding: '5px 10px', borderRadius: 6,
              background: 'var(--sf2)', border: '1px solid var(--b)',
              color: 'var(--tx2)', fontSize: 11, cursor: 'pointer',
            }}
          >
            Refresh
          </button>
        </div>
      </div>

      {/* Module list */}
      {modules.length === 0 ? (
        <div style={{
          textAlign: 'center', padding: '40px 20px',
          color: 'var(--tx3)', fontSize: 11,
          background: 'var(--sf)', borderRadius: 'var(--r)',
          border: '1px solid var(--b)',
        }}>
          No modules installed yet
        </div>
      ) : (
        modules.map((mod) => <ModuleCard key={mod.name} mod={mod} />)
      )}

      {/* Install from ZIP */}
      <div
        onClick={() => !installing && fileRef.current?.click()}
        style={{
          border: '1.5px dashed var(--b2)',
          borderRadius: 'var(--r)',
          padding: '18px',
          textAlign: 'center',
          color: installMsg ? (installMsg.startsWith('✓') ? 'var(--gr)' : installMsg.startsWith('✗') ? 'var(--rd)' : 'var(--tx2)') : 'var(--tx3)',
          fontSize: 11,
          marginTop: 4,
          cursor: installing ? 'default' : 'pointer',
          transition: 'border-color .15s',
        }}
      >
        {installMsg || '+ Install module from ZIP'}
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
