import { useState } from 'react';
import { useStore } from '../store/useStore';
import type { Device } from '../store/useStore';

/* ── helpers ─────────────────────────────────────── */
function deviceIsOn(device: Device): boolean {
    const s = device.state as Record<string, unknown>;
    if (s.on !== undefined) return Boolean(s.on);
    if (s.power !== undefined) return Boolean(s.power);
    if (s.active !== undefined) return Boolean(s.active);
    if (s.state !== undefined) return s.state === 'on' || s.state === true;
    return false;
}

/* ── Scene definitions ───────────────────────────── */
const SCENES = [
    { key: 'morning', icon: '🌅', label: 'Morning', desc: 'Bright lights · 22°C · Blinds up', bg: 'var(--blue-bg)', color: 'var(--accent)' },
    { key: 'work', icon: '☀️', label: 'Work', desc: 'Focus light · 21°C · DND mode', bg: 'var(--amber-bg)', color: 'var(--amber)' },
    { key: 'movie', icon: '🎬', label: 'Movie', desc: 'Dim lights · 23°C · Blinds down', bg: 'var(--purple-bg)', color: 'var(--purple)' },
    { key: 'dinner', icon: '🍽️', label: 'Dinner', desc: 'Warm lights · 21°C · Fan off', bg: 'var(--teal-bg)', color: 'var(--teal)' },
    { key: 'relax', icon: '🛋️', label: 'Relax', desc: 'Soft lights · 22°C', bg: 'var(--green-bg)', color: 'var(--green)' },
    { key: 'night', icon: '🌙', label: 'Night', desc: 'Lights off · 19°C · Alarm on', bg: 'var(--surface3)', color: 'var(--text3)' },
];

/* ── Quick-control categories ────────────────────── */
const CTRL_GROUPS = [
    {
        label: 'Lighting',
        icon: '💡',
        controls: [
            { id: 'lights_all', label: 'All lights', init: true },
            { id: 'lights_hall', label: 'Hallway', init: false },
            { id: 'lights_living', label: 'Living room', init: true },
            { id: 'lights_bed', label: 'Bedroom', init: false },
        ],
    },
    {
        label: 'Climate',
        icon: '🌡',
        controls: [
            { id: 'heat', label: 'Heating', init: true },
            { id: 'cool', label: 'Cooling', init: false },
            { id: 'fan', label: 'Ventilation', init: true },
            { id: 'humidif', label: 'Humidifier', init: false },
        ],
    },
    {
        label: 'Security',
        icon: '🔒',
        controls: [
            { id: 'alarm', label: 'Alarm', init: false },
            { id: 'cam', label: 'Cameras', init: true },
            { id: 'motion', label: 'Motion sens', init: true },
            { id: 'doorbell', label: 'Doorbell', init: true },
        ],
    },
    {
        label: 'Energy',
        icon: '⚡',
        controls: [
            { id: 'standby', label: 'Standby off', init: false },
            { id: 'solar', label: 'Solar mode', init: true },
            { id: 'ev', label: 'EV charging', init: false },
            { id: 'eco', label: 'Eco mode', init: true },
        ],
    },
];

/* ── Main component ─────────────────────────────── */
export default function Scenes() {
    const devices = useStore((s) => s.devices);
    const updateDeviceState = useStore((s) => s.updateDeviceState);
    const [activeScene, setActiveScene] = useState('morning');

    // Stub toggle state (not connected to backend — stubs for future automation module)
    const [toggles, setToggles] = useState<Record<string, boolean>>(() => {
        const init: Record<string, boolean> = {};
        CTRL_GROUPS.forEach((g) => g.controls.forEach((c) => { init[c.id] = c.init; }));
        return init;
    });

    const actuators = devices.filter((d) => d.type === 'actuator' || d.type === 'virtual');

    return (
        <div className="thin-scroll" style={{
            height: '100%', overflowY: 'auto', padding: 14,
            display: 'grid', gridTemplateColumns: '1fr 200px', gap: 12, alignContent: 'start'
        }}>

            {/* ── LEFT ──────────────────────────────────── */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10, minWidth: 0 }}>

                {/* Scene selector */}
                <div className="card" style={{ padding: '10px 12px' }}>
                    <div className="card-header"><div className="card-title">Scenes</div></div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 7 }}>
                        {SCENES.map((s) => (
                            <div key={s.key}
                                className={`scene-chip${activeScene === s.key ? ' active' : ''}`}
                                style={{ flexDirection: 'column', alignItems: 'flex-start', padding: '10px 10px 8px', gap: 5 }}
                                onClick={() => setActiveScene(s.key)}>
                                <div className="scene-chip-icon" style={{ background: s.bg }}>{s.icon}</div>
                                <div className="scene-chip-text" style={{ fontWeight: 600 }}>{s.label}</div>
                                <div style={{ fontSize: 10, color: 'var(--text3)', lineHeight: 1.3 }}>{s.desc}</div>
                            </div>
                        ))}
                    </div>
                </div>

                {/* Quick controls grid */}
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                    {CTRL_GROUPS.map((group) => (
                        <div key={group.label} className="card" style={{ padding: '10px 12px' }}>
                            <div className="card-header">
                                <div className="card-title">{group.icon} {group.label}</div>
                            </div>
                            <div style={{ display: 'flex', flexDirection: 'column' }}>
                                {group.controls.map((ctrl) => (
                                    <div key={ctrl.id} className="toggle-row"
                                        onClick={() => setToggles((prev) => ({ ...prev, [ctrl.id]: !prev[ctrl.id] }))}>
                                        <span style={{ flex: 1, fontSize: 11, color: 'var(--text2)' }}>{ctrl.label}</span>
                                        <div className={`toggle-switch ${toggles[ctrl.id] ? 'on' : 'off'}`}>
                                            <div className="toggle-knob" />
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </div>
                    ))}
                </div>

                {/* Real device toggles (from registry) */}
                {actuators.length > 0 && (
                    <div className="card" style={{ padding: '10px 12px' }}>
                        <div className="card-header">
                            <div className="card-title">Device controls</div>
                            <span style={{ fontSize: 10, color: 'var(--text3)' }}>from registry</span>
                        </div>
                        <div style={{ display: 'flex', flexDirection: 'column' }}>
                            {actuators.slice(0, 6).map((d) => {
                                const on = deviceIsOn(d);
                                return (
                                    <div key={d.device_id} className="toggle-row"
                                        onClick={() => updateDeviceState(d.device_id, { on: !on })}>
                                        <div style={{
                                            width: 24, height: 24, borderRadius: 6,
                                            background: on ? 'var(--amber-bg)' : 'var(--surface3)',
                                            display: 'flex', alignItems: 'center', justifyContent: 'center'
                                        }}>
                                            <div style={{
                                                width: 8, height: 8, borderRadius: '50%',
                                                background: on ? 'var(--amber)' : 'var(--text3)'
                                            }} />
                                        </div>
                                        <span style={{ flex: 1, fontSize: 11, color: 'var(--text2)' }}>{d.name}</span>
                                        <div className={`toggle-switch ${on ? 'on' : 'off'}`}>
                                            <div className="toggle-knob" />
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    </div>
                )}
            </div>

            {/* ── RIGHT ─────────────────────────────────── */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10, minWidth: 0 }}>
                {/* Active scene card */}
                {(() => {
                    const s = SCENES.find((x) => x.key === activeScene)!;
                    return (
                        <div className="card" style={{ padding: '14px 12px', textAlign: 'center' }}>
                            <div style={{ fontSize: 28, marginBottom: 8 }}>{s.icon}</div>
                            <div style={{ fontSize: 13, fontWeight: 600, color: s.color, marginBottom: 4 }}>{s.label}</div>
                            <div style={{ fontSize: 10, color: 'var(--text3)', lineHeight: 1.5 }}>{s.desc}</div>
                            <button style={{
                                marginTop: 12, width: '100%', background: s.bg,
                                border: `1px solid ${s.color}44`, borderRadius: 8,
                                padding: '7px 0', fontSize: 11, color: s.color, cursor: 'pointer',
                            }}>
                                Activate
                            </button>
                        </div>
                    );
                })()}

                {/* Schedules stub */}
                <div className="card" style={{ padding: '10px 12px' }}>
                    <div className="card-header"><div className="card-title">Schedules</div></div>
                    {[
                        { time: '07:00', label: 'Morning', days: 'Mon–Fri' },
                        { time: '22:30', label: 'Night', days: 'Every day' },
                    ].map((sch) => (
                        <div key={sch.time} style={{
                            display: 'flex', alignItems: 'center', gap: 8,
                            padding: '6px 0', borderBottom: '1px solid var(--border)'
                        }}>
                            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--accent)', width: 38 }}>
                                {sch.time}
                            </div>
                            <div style={{ flex: 1 }}>
                                <div style={{ fontSize: 11, color: 'var(--text)' }}>{sch.label}</div>
                                <div style={{ fontSize: 10, color: 'var(--text3)' }}>{sch.days}</div>
                            </div>
                            <div className="toggle-switch on"><div className="toggle-knob" /></div>
                        </div>
                    ))}
                    <div style={{ marginTop: 8, fontSize: 10, color: 'var(--text3)', textAlign: 'center', opacity: 0.6 }}>
                        Automation module required
                    </div>
                </div>

                {/* Activity stub */}
                <div className="card" style={{ padding: '10px 12px' }}>
                    <div className="card-header"><div className="card-title">Activity</div></div>
                    {[
                        { color: 'var(--green)', text: 'Morning activated' },
                        { color: 'var(--amber)', text: 'Heating toggle' },
                        { color: 'var(--accent)', text: 'Scene changed' },
                    ].map((ev, i) => (
                        <div key={i} className="event-item">
                            <div className="event-dot" style={{ background: ev.color }} />
                            <div className="event-text">{ev.text}</div>
                            <div className="event-time">—</div>
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
}
