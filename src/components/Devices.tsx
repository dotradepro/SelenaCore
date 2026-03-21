import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useStore } from '../store/useStore';
import type { Device } from '../store/useStore';

/* ── helpers ──────────────────────────────────────── */
function deviceIsOn(device: Device): boolean | null {
    const s = device.state as Record<string, unknown>;
    if (s.on !== undefined) return Boolean(s.on);
    if (s.power !== undefined) return Boolean(s.power);
    if (s.active !== undefined) return Boolean(s.active);
    if (s.state !== undefined) return s.state === 'on' || s.state === true;
    return null;
}

function formatLastSeen(ts: number | null, t: (k: string, o?: Record<string, unknown>) => string): string {
    if (!ts) return t('common.never');
    const diff = Math.floor(Date.now() / 1000 - ts);
    if (diff < 60) return t('common.secondsAgo', { count: diff });
    if (diff < 3600) return t('common.minutesAgo', { count: Math.floor(diff / 60) });
    if (diff < 86400) return t('common.hoursAgo', { count: Math.floor(diff / 3600) });
    return t('common.daysAgo', { count: Math.floor(diff / 86400) });
}

function statePreview(state: Record<string, unknown>): string {
    const entries = Object.entries(state).slice(0, 3);
    if (!entries.length) return '';
    return entries.map(([k, v]) => `${k}: ${JSON.stringify(v)}`).join(' · ');
}

const TYPE_COLOR: Record<string, { fg: string; bg: string }> = {
    sensor: { fg: 'var(--accent)', bg: 'var(--blue-bg)' },
    actuator: { fg: 'var(--amber)', bg: 'var(--amber-bg)' },
    controller: { fg: 'var(--purple)', bg: 'var(--purple-bg)' },
    virtual: { fg: 'var(--teal)', bg: 'var(--teal-bg)' },
};
const TYPE_ICON_PATH: Record<string, string> = {
    sensor: 'M7 2a3 3 0 00-3 3v5a3 3 0 006 0V5a3 3 0 00-3-3z M4.5 13c.8 1.2 2.2 2 3.5 2s2.7-.8 3.5-2',
    actuator: 'M7 2l1.5 3h3L9 7l1 3-3-2-3 2 1-3-2.5-2h3L7 2z',
    controller: 'M3 7h8M3 10h8M9 4l3 3-3 3',
    virtual: 'M7 2a5 5 0 100 10A5 5 0 007 2zm0 0v5m0 0l3-2',
};

/* ── Main component ─────────────────────────────── */
export default function Devices() {
    const { t } = useTranslation();
    const devices = useStore((s) => s.devices);
    const devicesLoading = useStore((s) => s.devicesLoading);
    const fetchDevices = useStore((s) => s.fetchDevices);
    const updateDeviceState = useStore((s) => s.updateDeviceState);
    const [search, setSearch] = useState('');
    const [typeFilter, setTypeFilter] = useState('all');

    useEffect(() => { fetchDevices(); }, [fetchDevices]);

    const types = ['all', ...Array.from(new Set(devices.map((d) => d.type)))];
    const filtered = devices.filter((d) => {
        const q = search.toLowerCase();
        const matchSearch = d.name.toLowerCase().includes(q) || d.protocol.toLowerCase().includes(q);
        const matchType = typeFilter === 'all' || d.type === typeFilter;
        return matchSearch && matchType;
    });

    return (
        <div className="thin-scroll" style={{ height: '100%', overflowY: 'auto', padding: 14 }}>

            {/* Header */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
                <div style={{ flex: 1 }}>
                    <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text)' }}>{t('devices.title')}</div>
                    <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>
                        {t('devices.registryInfo', { count: devices.length })}
                    </div>
                </div>
                <button onClick={() => fetchDevices()} style={{
                    background: 'var(--surface2)', border: '1px solid var(--border)',
                    borderRadius: 8, padding: '6px 12px', fontSize: 11, color: 'var(--text2)', cursor: 'pointer',
                }}>
                    {devicesLoading ? '…' : '↺ Refresh'}
                </button>
            </div>

            {/* Filter + search row */}
            <div style={{ display: 'flex', gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>
                <div style={{ position: 'relative', flex: 1, minWidth: 160 }}>
                    <svg viewBox="0 0 14 14" fill="none" width="13" height="13"
                        style={{ position: 'absolute', left: 9, top: '50%', transform: 'translateY(-50%)', color: 'var(--text3)' }}>
                        <circle cx="6" cy="6" r="4" stroke="currentColor" strokeWidth="1.3" />
                        <path d="M9 9l2.5 2.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
                    </svg>
                    <input
                        type="text"
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        placeholder={t('devices.searchPlaceholder')}
                        style={{
                            width: '100%', background: 'var(--surface2)', border: '1px solid var(--border)',
                            borderRadius: 8, padding: '6px 10px 6px 28px', fontSize: 11,
                            color: 'var(--text)', outline: 'none', boxSizing: 'border-box',
                        }}
                    />
                </div>
                <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
                    {types.map((tp) => (
                        <button key={tp} onClick={() => setTypeFilter(tp)} style={{
                            padding: '5px 10px', borderRadius: 20, fontSize: 11, fontWeight: 500, cursor: 'pointer',
                            background: typeFilter === tp ? 'var(--surface3)' : 'var(--surface2)',
                            border: `1px solid ${typeFilter === tp ? 'var(--border2)' : 'var(--border)'}`,
                            color: typeFilter === tp ? 'var(--text)' : 'var(--text2)',
                        }}>
                            {tp === 'all' ? t('common.all') : tp}
                        </button>
                    ))}
                </div>
            </div>

            {/* Empty states */}
            {devicesLoading && filtered.length === 0 && (
                <div style={{ textAlign: 'center', padding: '40px 0', color: 'var(--text3)', fontSize: 12 }}>
                    {t('common.loading')}
                </div>
            )}
            {!devicesLoading && filtered.length === 0 && (
                <div style={{ textAlign: 'center', padding: '40px 0', color: 'var(--text3)', fontSize: 12 }}>
                    {devices.length === 0 ? t('devices.noDevicesRegistered') : t('devices.noFilterResults')}
                </div>
            )}

            {/* Device list */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {filtered.map((device) => {
                    const col = TYPE_COLOR[device.type] ?? { fg: 'var(--text3)', bg: 'var(--surface3)' };
                    const path = TYPE_ICON_PATH[device.type] ?? TYPE_ICON_PATH.virtual;
                    const on = deviceIsOn(device);
                    const preview = statePreview(device.state as Record<string, unknown>);
                    return (
                        <div key={device.device_id} style={{
                            background: 'var(--surface)', border: '1px solid var(--border)',
                            borderRadius: 'var(--radius-sm)', padding: '10px 12px',
                            display: 'flex', alignItems: 'center', gap: 10,
                        }}>
                            {/* Type icon */}
                            <div style={{
                                width: 34, height: 34, borderRadius: 9, background: col.bg,
                                display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
                            }}>
                                <svg viewBox="0 0 14 14" fill="none" width="14" height="14" style={{ color: col.fg }}>
                                    <path d={path} stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
                                </svg>
                            </div>

                            {/* Info */}
                            <div style={{ flex: 1, minWidth: 0 }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2 }}>
                                    <span style={{
                                        fontSize: 12, fontWeight: 500, color: 'var(--text)',
                                        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap'
                                    }}>
                                        {device.name}
                                    </span>
                                    <span style={{
                                        fontSize: 9, padding: '2px 6px', borderRadius: 4,
                                        background: 'var(--surface3)', color: 'var(--text3)', flexShrink: 0,
                                    }}>{device.protocol}</span>
                                    <span style={{ fontSize: 9, color: col.fg, flexShrink: 0 }}>{device.type}</span>
                                </div>
                                {preview && (
                                    <div style={{
                                        fontSize: 10, color: 'var(--text3)', fontFamily: 'var(--font-mono)',
                                        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap'
                                    }}>
                                        {preview}
                                    </div>
                                )}
                            </div>

                            {/* Last seen */}
                            <div style={{ fontSize: 10, color: 'var(--text3)', flexShrink: 0, minWidth: 60, textAlign: 'right' }}>
                                {formatLastSeen(device.last_seen, t)}
                            </div>

                            {/* Toggle / state indicator */}
                            {on !== null ? (
                                <div className={`toggle-switch ${on ? 'on' : 'off'}`}
                                    onClick={() => updateDeviceState(device.device_id, { on: !on })}>
                                    <div className="toggle-knob" />
                                </div>
                            ) : (
                                <div style={{ width: 32, textAlign: 'center', fontSize: 11, color: 'var(--text3)' }}>—</div>
                            )}
                        </div>
                    );
                })}
            </div>
        </div>
    );
}


