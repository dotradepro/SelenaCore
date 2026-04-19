import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Search, RefreshCw } from 'lucide-react';
import { useStore } from '../store/useStore';
import type { Device } from '../store/useStore';
import { Button, Input, Toggle, Badge, EmptyState } from './ui';

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

type Tone = 'info' | 'warn' | 'pr' | 'neutral';

const TYPE_TONE: Record<string, Tone> = {
    sensor: 'info',
    actuator: 'warn',
    controller: 'pr',
    virtual: 'neutral',
};

const TYPE_ICON_BG: Record<Tone, string> = {
    info: 'rgba(79,140,247,.14)',
    warn: 'rgba(245,169,58,.14)',
    pr: 'rgba(155,110,244,.16)',
    neutral: 'var(--sf3)',
};

const TYPE_ICON_FG: Record<Tone, string> = {
    info: 'var(--ac)',
    warn: 'var(--am)',
    pr: 'var(--pu)',
    neutral: 'var(--tx3)',
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
    const showToast = useStore((s) => s.showToast);
    const [search, setSearch] = useState('');
    const [typeFilter, setTypeFilter] = useState('all');

    async function handleToggle(deviceId: string, newOn: boolean) {
        try {
            await updateDeviceState(deviceId, { on: newOn });
            showToast?.(t('devices.stateUpdated'), 'success');
        } catch {
            showToast?.(t('devices.updateFailed'), 'error');
        }
    }

    useEffect(() => { fetchDevices(); }, [fetchDevices]);

    const types = ['all', ...Array.from(new Set(devices.map((d) => d.type)))];
    const filtered = devices.filter((d) => {
        const q = search.toLowerCase();
        const matchSearch = d.name.toLowerCase().includes(q) || d.protocol.toLowerCase().includes(q);
        const matchType = typeFilter === 'all' || d.type === typeFilter;
        return matchSearch && matchType;
    });

    return (
        <div className="generic-page">
            {/* Header */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
                <div style={{ flex: 1 }}>
                    <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--tx)' }}>{t('devices.title')}</div>
                    <div style={{ fontSize: 11, color: 'var(--tx3)', marginTop: 2 }}>
                        {t('devices.registryInfo', { count: devices.length })}
                    </div>
                </div>
                <Button
                    variant="secondary"
                    size="sm"
                    loading={devicesLoading}
                    onClick={() => fetchDevices()}
                    leftIcon={<RefreshCw size={12} />}
                >
                    {t('common.refresh')}
                </Button>
            </div>

            {/* Filter + search row */}
            <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
                <div style={{ position: 'relative', flex: 1, minWidth: 160 }}>
                    <Search
                        size={13}
                        style={{
                            position: 'absolute', left: 9, top: '50%',
                            transform: 'translateY(-50%)', color: 'var(--tx3)',
                            pointerEvents: 'none',
                        }}
                    />
                    <Input
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        placeholder={t('devices.searchPlaceholder')}
                        style={{ paddingLeft: 28 }}
                    />
                </div>
                <div className="chip-picker" style={{ flexShrink: 0 }}>
                    {types.map((tp) => (
                        <button
                            key={tp}
                            type="button"
                            className={`chip ${typeFilter === tp ? 'on' : ''}`}
                            onClick={() => setTypeFilter(tp)}
                        >
                            {tp === 'all' ? t('common.all') : tp}
                        </button>
                    ))}
                </div>
            </div>

            {/* Empty / loading states */}
            {devicesLoading && filtered.length === 0 && (
                <EmptyState>{t('common.loading')}</EmptyState>
            )}
            {!devicesLoading && filtered.length === 0 && (
                <EmptyState>
                    {devices.length === 0 ? t('devices.noDevicesRegistered') : t('devices.noFilterResults')}
                </EmptyState>
            )}

            {/* Device list */}
            <div className="list">
                {filtered.map((device) => {
                    const tone: Tone = TYPE_TONE[device.type] ?? 'neutral';
                    const path = TYPE_ICON_PATH[device.type] ?? TYPE_ICON_PATH.virtual;
                    const on = deviceIsOn(device);
                    const preview = statePreview(device.state as Record<string, unknown>);
                    return (
                        <div key={device.device_id} className="list-row">
                            <div
                                style={{
                                    width: 34, height: 34, borderRadius: 9,
                                    background: TYPE_ICON_BG[tone],
                                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                                    flexShrink: 0,
                                }}
                            >
                                <svg viewBox="0 0 14 14" fill="none" width="14" height="14" style={{ color: TYPE_ICON_FG[tone] }}>
                                    <path d={path} stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
                                </svg>
                            </div>

                            <div className="lr-main">
                                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2, flexWrap: 'wrap' }}>
                                    <span className="lr-title" style={{ flexShrink: 1, minWidth: 0 }}>{device.name}</span>
                                    <Badge tone="neutral">{device.protocol}</Badge>
                                    <Badge tone={tone}>{device.type}</Badge>
                                    {device.location && <Badge tone="info">{device.location}</Badge>}
                                </div>
                                {preview && (
                                    <div style={{
                                        fontSize: 10, color: 'var(--tx3)', fontFamily: 'var(--font-mono)',
                                        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                                    }}>
                                        {preview}
                                    </div>
                                )}
                            </div>

                            <div style={{ fontSize: 10, color: 'var(--tx3)', flexShrink: 0, minWidth: 60, textAlign: 'right' }}>
                                {formatLastSeen(device.last_seen, t)}
                            </div>

                            {on !== null ? (
                                <Toggle
                                    size="sm"
                                    checked={on}
                                    onChange={() => handleToggle(device.device_id, !on)}
                                    label={device.name}
                                />
                            ) : (
                                <div style={{ width: 46, textAlign: 'center', fontSize: 11, color: 'var(--tx3)' }}>—</div>
                            )}
                        </div>
                    );
                })}
            </div>
        </div>
    );
}
