import { useEffect, useState, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useStore, Module } from '../store/useStore';

/* ── Widget Shell ── */
function WidgetShell({ mod, editMode }: { mod: Module; editMode: boolean }) {
  const navigate = useNavigate();
  const isRunning = mod.status === 'RUNNING';
  const isErr = mod.status === 'ERROR';
  const dotCls = isRunning ? 'wsd-run' : isErr ? 'wsd-err' : 'wsd-stop';
  const initial = (mod.name || '?')[0].toUpperCase();

  // Use widget size from manifest, with fallback heuristic
  const widgetSize = mod.ui?.widget?.size;
  const span = widgetSize ? `sp-${widgetSize}` : (mod.type === 'SYSTEM' ? 'sp-1x1' : 'sp-2x1');

  // Listen for openSettings postMessage from widget iframe
  useEffect(() => {
    function handleMsg(e: MessageEvent) {
      if (e.data?.type === 'openSettings' && e.data?.module === mod.name) {
        navigate(`/modules/${mod.name}`);
      }
    }
    window.addEventListener('message', handleMsg);
    return () => window.removeEventListener('message', handleMsg);
  }, [mod.name, navigate]);

  return (
    <div className={`ws ${span}${editMode ? ' edit-mode' : ''}`}>
      <div className={`ws-dot ${dotCls}`} />
      {editMode && <div className="ws-remove">−</div>}
      {isRunning ? (
        <iframe
          src={`/api/ui/modules/${mod.name}/widget`}
          sandbox="allow-scripts allow-same-origin"
          scrolling="no"
          title={mod.name}
          style={{ width: '100%', height: '100%', border: 'none', display: 'block', pointerEvents: 'auto' }}
        />
      ) : (
        <div
          style={{
            width: '100%', height: '100%',
            display: 'flex', flexDirection: 'column',
            alignItems: 'center', justifyContent: 'center',
            gap: 5, opacity: .4, cursor: 'pointer',
          }}
          onClick={() => navigate(`/modules/${mod.name}`)}
        >
          <div style={{
            width: 32, height: 32, borderRadius: 9,
            background: 'var(--sf2)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 14, fontWeight: 500, color: 'var(--tx3)',
          }}>
            {initial}
          </div>
          <div style={{ fontSize: 9, fontWeight: 500, textTransform: 'uppercase', letterSpacing: '.05em', color: 'var(--tx3)' }}>
            {mod.name}
          </div>
          <div style={{ fontSize: 9, color: 'var(--tx3)' }}>Stopped</div>
        </div>
      )}
      <div className="ws-label">{mod.name} · :{mod.port}</div>
    </div>
  );
}

/* ── Voice System Widget (pinned, always screen 0) ── */
function VoiceSystemWidget() {
  const voiceStatus = useStore((s) => s.voiceStatus);
  const setVoiceStatus = useStore((s) => s.setVoiceStatus);
  const [open, setOpen] = useState(false);
  const [transcript, setTranscript] = useState('');
  const [vstatus, setVstatus] = useState('Ready');

  const isListening = voiceStatus === 'listening';
  const isSpeaking = voiceStatus === 'speaking';
  const statusColor = isListening ? 'var(--gr)' : isSpeaking ? 'var(--ac)' : 'var(--tx3)';
  const statusLabel = isListening ? 'Listening…' : isSpeaking ? 'Speaking…' : 'Ready';

  function triggerListen() {
    if (voiceStatus === 'listening') {
      setVoiceStatus('idle');
      setVstatus('Ready');
      return;
    }
    setVoiceStatus('listening');
    setVstatus('Listening…');
    setTranscript('');
    setTimeout(() => {
      setTranscript('«turn on kitchen lights»');
      setVoiceStatus('speaking');
      setVstatus('Speaking…');
      setTimeout(() => {
        setVoiceStatus('idle');
        setVstatus('✓ Done');
        setTranscript('Kitchen lights on (3 lamps, 70%)');
      }, 1200);
    }, 2200);
  }

  function closeModal() {
    setOpen(false);
    if (voiceStatus !== 'idle') {
      setVoiceStatus('idle');
      setVstatus('Ready');
      setTranscript('');
    }
  }

  return (
    <>
      {/* Widget card in grid */}
      <div
        className="ws sp-2x2"
        style={{ cursor: 'pointer' }}
        onClick={() => setOpen(true)}
      >
        <div className="ws-dot wsd-run" />
        <div style={{
          width: '100%', height: '100%',
          display: 'flex', flexDirection: 'column',
          alignItems: 'center', justifyContent: 'space-between',
          padding: '10px 8px 8px',
        }}>
          {/* Label row */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 5, alignSelf: 'flex-start' }}>
            <div style={{ fontSize: 9, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.07em', color: 'var(--tx3)' }}>
              Voice · Core
            </div>
            <div style={{
              width: 5, height: 5, borderRadius: '50%',
              background: statusColor,
              boxShadow: voiceStatus !== 'idle' ? `0 0 5px ${statusColor}` : 'none',
              transition: 'all .3s',
            }} />
          </div>

          {/* Mic ring */}
          <div
            className={`vring${isListening ? ' listening' : ''}`}
            style={{ width: 52, height: 52 }}
            onClick={(e) => { e.stopPropagation(); triggerListen(); }}
          >
            <svg viewBox="0 0 26 26" fill="none" width="20" height="20">
              <rect x="9" y="2" width="8" height="13" rx="4" stroke="currentColor" strokeWidth="1.5" />
              <path d="M4.5 12c0 4.7 3.8 8.5 8.5 8.5S21.5 16.7 21.5 12M13 20.5V24M9 24h8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </div>

          {/* Status */}
          <div style={{ fontSize: 10, color: statusColor, transition: 'color .3s' }}>
            {statusLabel}
          </div>

          {/* Transcript preview */}
          <div style={{
            fontSize: 9, color: 'var(--tx2)',
            textAlign: 'center', maxWidth: '90%',
            overflow: 'hidden', whiteSpace: 'nowrap',
            textOverflow: 'ellipsis', minHeight: 11,
          }}>
            {transcript}
          </div>
        </div>
        <div className="ws-label">Voice · System Module</div>
      </div>

      {/* Full voice popup modal */}
      {open && (
        <div
          className="vov open"
          style={{ position: 'fixed' }}
          onClick={(e) => { if (e.target === e.currentTarget) closeModal(); }}
        >
          <div className="vmodal">
            <div style={{ fontSize: 10, fontWeight: 500, color: 'var(--tx3)', textTransform: 'uppercase', letterSpacing: '.06em', alignSelf: 'flex-start' }}>
              Voice Assistant · SelenaCore
            </div>
            <div className={`vring${isListening ? ' listening' : ''}`} onClick={triggerListen}>
              <svg viewBox="0 0 26 26" fill="none" width="26" height="26">
                <rect x="9" y="2" width="8" height="13" rx="4" stroke="currentColor" strokeWidth="1.5" />
                <path d="M4.5 12c0 4.7 3.8 8.5 8.5 8.5S21.5 16.7 21.5 12M13 20.5V24M9 24h8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
              </svg>
            </div>
            <div style={{ fontSize: 12, color: isListening ? 'var(--ac)' : vstatus.startsWith('✓') ? 'var(--gr)' : 'var(--tx2)' }}>
              {vstatus}
            </div>
            {transcript && (
              <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--tx)', textAlign: 'center' }}>
                {transcript}
              </div>
            )}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, justifyContent: 'center' }}>
              {['Turn on lights', 'Set 22° climate', 'Night scene', 'Status report', 'Lock front door'].map((s) => (
                <div key={s} className="vsug" onClick={() => {
                  setTranscript(`«${s.toLowerCase()}»`);
                  setVstatus('Processing…');
                  setVoiceStatus('speaking');
                  setTimeout(() => {
                    setVstatus('✓ Done');
                    setVoiceStatus('idle');
                    setTranscript(s);
                  }, 800);
                }}>
                  {s}
                </div>
              ))}
            </div>
            <div
              style={{ fontSize: 10, color: 'var(--tx3)', cursor: 'pointer', marginTop: 2 }}
              onClick={closeModal}
            >
              ✕ close
            </div>
          </div>
        </div>
      )}
    </>
  );
}

/* ── Dashboard (Widget Homescreen) ── */
export default function Dashboard() {
  const modules = useStore((s) => s.modules);
  const fetchModules = useStore((s) => s.fetchModules);

  const [currentScreen, setCurrentScreen] = useState(0);
  const [editMode, setEditMode] = useState(false);
  const swipeRef = useRef<number | null>(null);

  useEffect(() => { fetchModules(); }, [fetchModules]);

  // Screen 0: VoiceSystemWidget (sp-2x2 = 4 cells) + up to 4 module widgets
  // Screen 1+: up to 6 module widgets
  const SLOTS_S0 = 4;
  const PER_SCREEN = 6;
  const allMods = modules;
  const overflow = Math.max(0, allMods.length - SLOTS_S0);
  const totalScreens = 1 + (overflow > 0 ? Math.ceil(overflow / PER_SCREEN) : 0);

  function getScreenMods(si: number): Module[] {
    if (si === 0) return allMods.slice(0, SLOTS_S0);
    const start = SLOTS_S0 + (si - 1) * PER_SCREEN;
    return allMods.slice(start, start + PER_SCREEN);
  }

  // Sidebar is 52px; total viewport 800px
  const SCREEN_W = 748;

  const go = (dir: number) =>
    setCurrentScreen((s) => Math.max(0, Math.min(totalScreens - 1, s + dir)));

  function onTouchStart(e: React.TouchEvent) { swipeRef.current = e.touches[0].clientX; }
  function onTouchEnd(e: React.TouchEvent) {
    if (swipeRef.current === null) return;
    const dx = e.changedTouches[0].clientX - swipeRef.current;
    if (Math.abs(dx) > 40) go(dx < 0 ? 1 : -1);
    swipeRef.current = null;
  }

  return (
    <div
      style={{ position: 'relative', width: '100%', height: '100%', overflow: 'hidden' }}
      onTouchStart={onTouchStart}
      onTouchEnd={onTouchEnd}
    >
      {/* Edit toggle */}
      <div style={{ position: 'absolute', top: 6, right: 6, zIndex: 30 }}>
        <div
          className={`edit-toggle${editMode ? ' on' : ''}`}
          onClick={() => setEditMode(!editMode)}
        >
          {editMode ? 'Done' : 'Edit'}
        </div>
      </div>

      {/* Left arrow */}
      {currentScreen > 0 && (
        <div className="pg-arrow left" onClick={() => go(-1)}>
          <svg viewBox="0 0 10 10" fill="none" width="12" height="12">
            <path d="M6.5 2L3.5 5l3 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>
      )}

      {/* Right arrow */}
      {currentScreen < totalScreens - 1 && (
        <div className="pg-arrow right" onClick={() => go(1)}>
          <svg viewBox="0 0 10 10" fill="none" width="12" height="12">
            <path d="M3.5 2L6.5 5l-3 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>
      )}

      {/* Screens track */}
      <div style={{
        display: 'flex',
        height: '100%',
        transition: 'transform .35s cubic-bezier(.4,0,.2,1)',
        transform: `translateX(-${currentScreen * SCREEN_W}px)`,
      }}>
        {Array.from({ length: totalScreens }, (_, si) => {
          const mods = getScreenMods(si);
          const maxSlots = si === 0 ? SLOTS_S0 : PER_SCREEN;
          return (
            <div key={si} style={{ flexShrink: 0, width: SCREEN_W, height: '100%', padding: 10 }}>
              <div style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(4, 1fr)',
                gridTemplateRows: 'repeat(3, 1fr)',
                gap: 8,
                width: '100%',
                height: 'calc(100% - 20px)',
              }}>
                {/* Voice system widget — always first on screen 0 */}
                {si === 0 && <VoiceSystemWidget />}

                {mods.map((mod) => (
                  <WidgetShell key={mod.name} mod={mod} editMode={editMode} />
                ))}

                {/* Add slot (edit mode) */}
                {editMode && mods.length < maxSlots && (
                  <div className="ws ws-empty sp-1x1">
                    <svg viewBox="0 0 16 16" fill="none" width="18" height="18" style={{ color: 'var(--tx3)' }}>
                      <circle cx="8" cy="8" r="6.5" stroke="currentColor" strokeWidth="1.3" />
                      <path d="M8 5v6M5 8h6" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
                    </svg>
                    <div style={{ fontSize: 9, color: 'var(--tx3)', marginTop: 2 }}>Add</div>
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Page dots */}
      {totalScreens > 1 && (
        <div style={{
          position: 'absolute', bottom: 6, left: '50%', transform: 'translateX(-50%)',
          display: 'flex', gap: 5, zIndex: 10, pointerEvents: 'none',
        }}>
          {Array.from({ length: totalScreens }, (_, i) => (
            <div
              key={i}
              style={{
                width: i === currentScreen ? 14 : 5,
                height: 5,
                borderRadius: i === currentScreen ? 3 : '50%',
                background: i === currentScreen ? 'var(--tx2)' : 'var(--tx3)',
                transition: 'all .25s',
                pointerEvents: 'auto',
                cursor: 'pointer',
              }}
              onClick={() => setCurrentScreen(i)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
