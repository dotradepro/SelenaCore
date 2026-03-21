import { useState } from 'react';

/* ── Static history stub ──────────────────────────── */
const HISTORY = [
    { role: 'user', text: 'Turn on the kitchen lights', time: '07:14' },
    { role: 'assistant', text: 'Done — kitchen lights are on.', time: '07:14' },
    { role: 'user', text: 'Set the thermostat to 22 degrees', time: '07:15' },
    { role: 'assistant', text: 'Thermostat set to 22°C.', time: '07:15' },
    { role: 'user', text: 'What\'s the temperature in the bedroom?', time: '08:03' },
    { role: 'assistant', text: 'Bedroom sensor reads 21.4°C.', time: '08:03' },
    { role: 'user', text: 'Activate movie scene', time: '19:45' },
    { role: 'assistant', text: 'Movie scene activated. Enjoy!', time: '19:45' },
];

const SUGGESTIONS = [
    '🔆  Turn on all lights',
    '🌡  Set temp 22°',
    '🌙  Activate night mode',
    '📊  System status',
    '🔒  Lock the front door',
    '🎬  Movie scene',
];

/* ── Main component ─────────────────────────────── */
export default function VoicePage() {
    const [listening, setListening] = useState(false);

    const toggle = () => {
        setListening(true);
        setTimeout(() => setListening(false), 4000);
    };

    return (
        <div className="thin-scroll" style={{
            height: '100%', overflowY: 'auto', padding: 14,
            display: 'grid', gridTemplateColumns: '1fr 220px', gap: 12, alignContent: 'start'
        }}>

            {/* ── LEFT: Voice history ─────────────────── */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10, minWidth: 0 }}>

                {/* History card */}
                <div className="card" style={{ padding: '10px 12px', flex: 1 }}>
                    <div className="card-header">
                        <div className="card-title">Voice history</div>
                        <span style={{ fontSize: 10, color: 'var(--text3)' }}>stub · not stored</span>
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                        {HISTORY.map((msg, i) => {
                            const isUser = msg.role === 'user';
                            return (
                                <div key={i} style={{
                                    display: 'flex', flexDirection: isUser ? 'row-reverse' : 'row',
                                    gap: 8, alignItems: 'flex-end',
                                }}>
                                    {/* Avatar */}
                                    <div style={{
                                        width: 26, height: 26, borderRadius: '50%', flexShrink: 0,
                                        background: isUser ? 'var(--blue-bg)' : 'var(--surface3)',
                                        display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 12,
                                    }}>
                                        {isUser ? '👤' : '🤖'}
                                    </div>
                                    {/* Bubble */}
                                    <div style={{
                                        maxWidth: '75%',
                                        background: isUser ? 'var(--blue-bg)' : 'var(--surface2)',
                                        border: `1px solid ${isUser ? 'rgba(79,142,247,0.2)' : 'var(--border)'}`,
                                        borderRadius: isUser ? '12px 12px 3px 12px' : '12px 12px 12px 3px',
                                        padding: '7px 10px',
                                    }}>
                                        <div style={{ fontSize: 11, color: 'var(--text)', lineHeight: 1.5 }}>{msg.text}</div>
                                        <div style={{
                                            fontSize: 9, color: 'var(--text3)', marginTop: 3,
                                            textAlign: isUser ? 'left' : 'right', fontFamily: 'var(--font-mono)'
                                        }}>
                                            {msg.time}
                                        </div>
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                </div>

                {/* Status bar */}
                <div style={{
                    background: 'var(--surface)', border: '1px solid var(--border)',
                    borderRadius: 'var(--radius-sm)', padding: '8px 12px',
                    display: 'flex', alignItems: 'center', gap: 8,
                }}>
                    <div style={{
                        width: 7, height: 7, borderRadius: '50%',
                        background: listening ? 'var(--red)' : 'var(--text3)',
                        animation: listening ? 'blink-dot 0.8s infinite' : 'none',
                    }} />
                    <span style={{ fontSize: 11, color: listening ? 'var(--text)' : 'var(--text3)' }}>
                        {listening ? 'Listening for your command…' : 'Ready · Tap the mic to start'}
                    </span>
                    <span style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--text3)' }}>
                        STT: Whisper.cpp · LLM: phi-3-mini
                    </span>
                </div>
            </div>

            {/* ── RIGHT: Mic + suggestions ─────────────── */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10, minWidth: 0 }}>

                {/* Mic card */}
                <div className="card" style={{
                    padding: '16px 12px', display: 'flex',
                    flexDirection: 'column', alignItems: 'center', textAlign: 'center'
                }}>
                    <div style={{
                        fontSize: 12, fontWeight: 500, color: 'var(--text3)',
                        marginBottom: 14, textTransform: 'uppercase', letterSpacing: '0.06em'
                    }}>
                        Voice Assistant
                    </div>
                    <button className={`voice-btn${listening ? ' listening' : ''}`} onClick={toggle}
                        style={{ marginBottom: 12 }}>
                        <svg viewBox="0 0 22 22" fill="none" width="22" height="22" style={{ color: 'var(--accent)' }}>
                            <rect x="7.5" y="2" width="7" height="11" rx="3.5" stroke="currentColor" strokeWidth="1.5" />
                            <path d="M4 10c0 3.87 3.13 7 7 7s7-3.13 7-7M11 17v3M7 20h8"
                                stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                        </svg>
                    </button>
                    <div style={{ fontSize: 11, color: listening ? 'var(--accent)' : 'var(--text3)', marginBottom: 4 }}>
                        {listening ? 'Listening…' : 'Tap to speak'}
                    </div>
                    {listening && (
                        <div style={{ display: 'flex', gap: 3, alignItems: 'flex-end', height: 24, marginTop: 4 }}>
                            {[6, 14, 10, 18, 8, 14, 6].map((h, i) => (
                                <div key={i} style={{
                                    width: 3, height: h, borderRadius: 2, background: 'var(--accent)',
                                    animation: 'blink-dot 0.5s infinite', animationDelay: `${i * 0.07}s`,
                                }} />
                            ))}
                        </div>
                    )}
                </div>

                {/* Suggestions */}
                <div className="card" style={{ padding: '10px 12px' }}>
                    <div className="card-header"><div className="card-title">Try saying</div></div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                        {SUGGESTIONS.map((cmd) => (
                            <div key={cmd} className="voice-cmd">{cmd}</div>
                        ))}
                    </div>
                </div>

                {/* Wake word settings stub */}
                <div className="card" style={{ padding: '10px 12px' }}>
                    <div className="card-header"><div className="card-title">Wake word</div></div>
                    <div style={{
                        display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0',
                        borderBottom: '1px solid var(--border)', marginBottom: 6
                    }}>
                        <span style={{ flex: 1, fontSize: 11, color: 'var(--text2)' }}>«Hey Selena»</span>
                        <div className="toggle-switch on"><div className="toggle-knob" /></div>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0' }}>
                        <span style={{ flex: 1, fontSize: 11, color: 'var(--text2)' }}>Privacy mode</span>
                        <div className="toggle-switch off"><div className="toggle-knob" /></div>
                    </div>
                    <div style={{ marginTop: 8, fontSize: 10, color: 'var(--text3)', opacity: 0.6, textAlign: 'center' }}>
                        Voice module required
                    </div>
                </div>
            </div>
        </div>
    );
}
