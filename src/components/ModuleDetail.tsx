import { useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useStore } from '../store/useStore';

export default function ModuleDetail() {
    const { name } = useParams<{ name: string }>();
    const navigate = useNavigate();
    const modules = useStore((s) => s.modules);
    const fetchModules = useStore((s) => s.fetchModules);
    const startModule = useStore((s) => s.startModule);
    const stopModule = useStore((s) => s.stopModule);

    useEffect(() => { fetchModules(); }, [fetchModules]);

    const mod = modules.find((m) => m.name === name);

    if (!mod) {
        return (
            <div style={{ padding: 24, color: 'var(--tx3)' }}>
                <div style={{ fontSize: 13 }}>Module "{name}" not found.</div>
                <button
                    onClick={() => navigate('/modules')}
                    style={{
                        marginTop: 12, padding: '6px 14px', borderRadius: 8,
                        background: 'var(--sf2)', border: '1px solid var(--b)',
                        color: 'var(--tx2)', cursor: 'pointer', fontSize: 12,
                    }}
                >
                    ← Back to Modules
                </button>
            </div>
        );
    }

    const isRunning = mod.status === 'RUNNING';
    const hasSettings = !!mod.ui?.settings;

    return (
        <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
            {/* Header bar */}
            <div style={{
                display: 'flex', alignItems: 'center', gap: 8,
                padding: '8px 12px',
                borderBottom: '1px solid var(--b)',
                background: 'var(--sf)',
                flexShrink: 0,
            }}>
                <button
                    onClick={() => navigate(-1)}
                    style={{
                        background: 'none', border: 'none', color: 'var(--tx3)',
                        cursor: 'pointer', padding: '4px 6px', borderRadius: 6,
                        fontSize: 14, lineHeight: 1,
                    }}
                >
                    ←
                </button>
                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--tx)' }}>
                    {mod.name}
                </div>
                <div style={{ fontSize: 10, color: 'var(--tx3)' }}>
                    v{mod.version} · {mod.type}
                </div>
                <div style={{ flex: 1 }} />
                <div style={{
                    fontSize: 10, fontWeight: 500, padding: '2px 8px',
                    borderRadius: 10,
                    background: isRunning ? 'rgba(46,201,138,.15)' : 'rgba(255,255,255,.06)',
                    color: isRunning ? 'var(--gr)' : 'var(--tx3)',
                }}>
                    {mod.status.toLowerCase()}
                </div>
                {!isRunning ? (
                    <button
                        onClick={() => startModule(mod.name)}
                        style={{
                            padding: '4px 12px', borderRadius: 8, fontSize: 11,
                            background: 'rgba(46,201,138,.1)', border: '1px solid rgba(46,201,138,.2)',
                            color: 'var(--gr)', cursor: 'pointer',
                        }}
                    >
                        Start
                    </button>
                ) : (
                    <button
                        onClick={() => stopModule(mod.name)}
                        style={{
                            padding: '4px 12px', borderRadius: 8, fontSize: 11,
                            background: 'rgba(224,84,84,.1)', border: '1px solid rgba(224,84,84,.2)',
                            color: 'var(--rd)', cursor: 'pointer',
                        }}
                    >
                        Stop
                    </button>
                )}
            </div>

            {/* Settings iframe or placeholder */}
            <div style={{ flex: 1, overflow: 'hidden' }}>
                {isRunning && hasSettings ? (
                    <iframe
                        src={`/api/ui/modules/${mod.name}/settings`}
                        style={{
                            width: '100%', height: '100%', border: 'none',
                            display: 'block', background: '#0a0a0a',
                        }}
                        title={`${mod.name} settings`}
                        allow="geolocation"
                    />
                ) : (
                    <div style={{
                        display: 'flex', flexDirection: 'column',
                        alignItems: 'center', justifyContent: 'center',
                        height: '100%', gap: 8, color: 'var(--tx3)',
                    }}>
                        <div style={{ fontSize: 32, opacity: .3 }}>
                            {(mod.name || '?')[0].toUpperCase()}
                        </div>
                        <div style={{ fontSize: 12 }}>
                            {!isRunning
                                ? 'Start the module to access settings'
                                : 'No settings page available'}
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}
