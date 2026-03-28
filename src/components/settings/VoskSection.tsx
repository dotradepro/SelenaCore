import { useState, useEffect, useCallback, useRef } from 'react';
import { RefreshCw, Download, Trash2, Check, Search, Loader2, Mic, Volume2, Send, Bot, MessageSquare } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { cn } from '../../lib/utils';

interface VoskModel {
  id: string;
  name: string;
  lang?: string;
  size_mb?: number;
  notes?: string;
  url?: string;
  installed?: boolean;
  active?: boolean;
}

export default function VoskSection() {
  const { t } = useTranslation();
  const [engineStatus, setEngineStatus] = useState<{ installed: boolean; version: string | null }>({ installed: false, version: null });
  const [installing, setInstalling] = useState(false);
  const [installAction, setInstallAction] = useState<string>('');
  const [installedModels, setInstalledModels] = useState<VoskModel[]>([]);
  const [catalogModels, setCatalogModels] = useState<VoskModel[]>([]);
  const [activeModel, setActiveModel] = useState('');
  const [langFilter, setLangFilter] = useState('');
  const [search, setSearch] = useState('');
  const [showCatalog, setShowCatalog] = useState(false);
  const [loadingCatalog, setLoadingCatalog] = useState(false);
  const [downloading, setDownloading] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // STT test flow
  const [recording, setRecording] = useState(false);
  const [countdown, setCountdown] = useState(0);
  const [recognizedText, setRecognizedText] = useState('');
  const [recordedAudioB64, setRecordedAudioB64] = useState('');
  const [peakLevel, setPeakLevel] = useState(0);
  const [sendingToTts, setSendingToTts] = useState(false);
  const countdownRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // LLM flow
  const [aiResponse, setAiResponse] = useState('');
  const [aiProvider, setAiProvider] = useState('');
  const [aiModel, setAiModel] = useState('');
  const [sendingToAi, setSendingToAi] = useState(false);
  const [speakingAiResponse, setSpeakingAiResponse] = useState(false);
  const [manualText, setManualText] = useState('');

  const checkStatus = useCallback(async () => {
    try {
      const res = await fetch('/api/ui/setup/vosk/status').then(r => r.json());
      setEngineStatus(res);
    } catch { /* ignore */ }
  }, []);

  const fetchInstalled = useCallback(async () => {
    try {
      const res = await fetch('/api/ui/setup/stt/models').then(r => r.json());
      setInstalledModels(res.models || []);
      setActiveModel(res.active || '');
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    checkStatus();
    fetchInstalled();
  }, [checkStatus, fetchInstalled]);

  const handleInstall = async () => {
    setInstalling(true);
    setInstallAction('install');
    try {
      await fetch('/api/ui/setup/vosk/install', { method: 'POST' });
      // Poll progress
      const poll = setInterval(async () => {
        const res = await fetch('/api/ui/setup/vosk/install-progress').then(r => r.json());
        if (!res.running) {
          clearInterval(poll);
          setInstalling(false);
          checkStatus();
        }
      }, 2000);
    } catch { setInstalling(false); }
  };

  const handleUninstall = async () => {
    setInstalling(true);
    setInstallAction('uninstall');
    try {
      await fetch('/api/ui/setup/vosk/uninstall', { method: 'POST' });
      const poll = setInterval(async () => {
        const res = await fetch('/api/ui/setup/vosk/install-progress').then(r => r.json());
        if (!res.running) {
          clearInterval(poll);
          setInstalling(false);
          checkStatus();
        }
      }, 2000);
    } catch { setInstalling(false); }
  };

  const loadCatalog = async () => {
    setShowCatalog(true);
    setLoadingCatalog(true);
    try {
      const res = await fetch('/api/ui/setup/stt/catalog').then(r => r.json());
      setCatalogModels(res.models || []);
    } catch { /* ignore */ }
    setLoadingCatalog(false);
  };

  const selectModel = async (modelId: string) => {
    setSaving(true);
    try {
      await fetch('/api/ui/setup/stt/select', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: modelId }),
      });
      setActiveModel(modelId);
    } catch { /* ignore */ }
    setSaving(false);
  };

  const downloadModel = async (modelId: string) => {
    setDownloading(modelId);
    try {
      await fetch('/api/ui/setup/stt/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: modelId }),
      });
      // Poll progress
      const poll = setInterval(async () => {
        const res = await fetch(`/api/ui/setup/stt/download-progress/${modelId}`).then(r => r.json());
        if (!res.running) {
          clearInterval(poll);
          setDownloading(null);
          fetchInstalled();
        }
      }, 3000);
    } catch { setDownloading(null); }
  };

  const deleteModel = async (modelId: string) => {
    try {
      await fetch('/api/ui/setup/stt/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: modelId }),
      });
      fetchInstalled();
    } catch { /* ignore */ }
  };

  // STT test: record → recognize
  const startSttTest = async () => {
    setRecording(true);
    setRecognizedText('');
    setRecordedAudioB64('');
    setPeakLevel(0);
    const dur = 4;
    setCountdown(dur);
    countdownRef.current = setInterval(() => {
      setCountdown(prev => {
        if (prev <= 1) {
          if (countdownRef.current) clearInterval(countdownRef.current);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);

    try {
      const res = await fetch('/api/ui/setup/stt/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ device: 'default', duration: dur }),
      });
      const data = await res.json();
      if (data.status === 'ok') {
        setRecognizedText(data.text || '(...)');
        setRecordedAudioB64(data.audio_b64 || '');
        setPeakLevel(data.peak_level || 0);
      } else {
        setRecognizedText(data.error || 'Error');
      }
    } catch {
      setRecognizedText('Connection error');
    }
    setRecording(false);
    if (countdownRef.current) clearInterval(countdownRef.current);
    setCountdown(0);
  };

  // Play recorded audio in browser
  const playRecordedAudio = () => {
    if (!recordedAudioB64) return;
    const audio = new Audio(`data:audio/wav;base64,${recordedAudioB64}`);
    audio.play();
  };

  // Send text to TTS and play
  const speakText = async (text: string) => {
    if (!text) return;
    setSendingToTts(true);
    try {
      const res = await fetch('/api/ui/setup/tts/speak', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, output_device: 'default' }),
      });
      if (res.ok) {
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const audio = new Audio(url);
        audio.onended = () => { URL.revokeObjectURL(url); setSendingToTts(false); };
        audio.onerror = () => { URL.revokeObjectURL(url); setSendingToTts(false); };
        await audio.play();
        return;
      }
    } catch { /* ignore */ }
    setSendingToTts(false);
  };

  // Send text to AI model
  const sendToAi = async (text: string) => {
    if (!text || text === '(...)') return;
    setSendingToAi(true);
    setAiResponse('');
    setAiProvider('');
    setAiModel('');
    try {
      const res = await fetch('/api/ui/setup/llm/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      }).then(r => r.json());
      if (res.status === 'ok') {
        setAiResponse(res.response || '(empty)');
        setAiProvider(res.provider || '');
        setAiModel(res.model || '');
      } else {
        setAiResponse(`Error: ${res.error || 'unknown'}`);
        setAiProvider(res.provider || '');
      }
    } catch {
      setAiResponse('Connection error');
    }
    setSendingToAi(false);
  };

  // Speak AI response
  const speakAiResponse = async () => {
    if (!aiResponse || aiResponse.startsWith('Error:') || aiResponse === '(empty)') return;
    setSpeakingAiResponse(true);
    await speakText(aiResponse);
    setSpeakingAiResponse(false);
  };

  // Full pipeline: recognized text → AI → speak
  const sendRecognizedToAi = () => sendToAi(recognizedText);
  const sendManualToAi = () => { if (manualText.trim()) sendToAi(manualText.trim()); };

  // Languages from catalog
  const languages = [...new Set(catalogModels.map(m => m.lang).filter(Boolean))].sort();
  const installedIds = new Set(installedModels.map(m => m.id));

  const filteredCatalog = catalogModels.filter(m => {
    if (installedIds.has(m.id)) return false;
    if (langFilter && m.lang !== langFilter) return false;
    if (search && !m.name.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  return (
    <div style={{ background: 'var(--sf)', border: '1px solid var(--b)', borderRadius: 12, padding: 20 }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
        <div>
          <h4 style={{ fontWeight: 600, fontSize: 15, color: 'var(--tx)' }}>{t('settings.sttModel')}</h4>
          <p style={{ fontSize: 12, color: 'var(--tx2)', marginTop: 2 }}>Vosk — {t('settings.voiceAssistantDesc')}</p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span className={cn("text-xs px-2 py-1 rounded-md font-medium",
            engineStatus.installed ? "text-emerald-500 bg-emerald-500/10" : "text-red-400 bg-red-500/10")}>
            {engineStatus.installed ? `${t('settings.installed')} v${engineStatus.version}` : t('settings.notInstalled')}
          </span>
        </div>
      </div>

      {/* Engine actions */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        {!engineStatus.installed ? (
          <button onClick={handleInstall} disabled={installing}
            className="text-xs px-3 py-1.5 rounded-lg bg-emerald-600 text-white hover:bg-emerald-500 disabled:opacity-50 flex items-center gap-1">
            {installing && installAction === 'install' ? <Loader2 size={12} className="animate-spin" /> : <Download size={12} />}
            {installing && installAction === 'install' ? t('settings.installing') : t('settings.installEngine')}
          </button>
        ) : (
          <button onClick={handleUninstall} disabled={installing}
            className="text-xs px-3 py-1.5 rounded-lg bg-red-600/20 text-red-400 hover:bg-red-600/30 disabled:opacity-50 flex items-center gap-1">
            {installing && installAction === 'uninstall' ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
            {installing && installAction === 'uninstall' ? t('settings.uninstalling') : t('settings.uninstallEngine')}
          </button>
        )}
        <button onClick={() => { checkStatus(); fetchInstalled(); }}
          className="text-xs text-zinc-400 hover:text-zinc-200 flex items-center gap-1">
          <RefreshCw size={12} /> {t('common.refresh')}
        </button>
      </div>

      {/* Installed models */}
      {installedModels.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 12, fontWeight: 500, color: 'var(--tx2)', marginBottom: 8 }}>{t('settings.installedModels')}</div>
          <div className="space-y-2">
            {installedModels.map(m => (
              <div key={m.id} className={cn("p-3 rounded-lg border flex items-center justify-between transition-all",
                m.id === activeModel ? "border-emerald-500 bg-emerald-500/10" : "border-zinc-800 bg-zinc-950")}>
                <div>
                  <div className="text-sm font-medium">{m.name}</div>
                  {m.size_mb !== undefined && <div className="text-xs text-zinc-500">{m.size_mb} MB</div>}
                </div>
                <div className="flex items-center gap-2">
                  <button onClick={() => selectModel(m.id)} disabled={m.id === activeModel || saving}
                    className={cn("text-xs px-3 py-1.5 rounded-lg transition-colors",
                      m.id === activeModel ? "bg-emerald-500/20 text-emerald-500" : "bg-zinc-800 text-zinc-300 hover:bg-zinc-700")}>
                    {m.id === activeModel ? <Check size={12} /> : t('settings.activate')}
                  </button>
                  <button onClick={() => deleteModel(m.id)} disabled={m.id === activeModel}
                    className="text-xs px-2 py-1.5 rounded-lg text-red-400 hover:bg-red-500/10 disabled:opacity-30">
                    <Trash2 size={12} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Voice Pipeline Test */}
      {engineStatus.installed && installedModels.length > 0 && (
        <div style={{ background: 'var(--sf2)', border: '1px solid var(--b2)', borderRadius: 10, padding: 16, marginBottom: 16 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--tx)', marginBottom: 12 }}>{t('settings.voicePipelineTest')}</div>

          {/* Step 1: Record & Recognize */}
          <div style={{ marginBottom: 14 }}>
            <div style={{ fontSize: 11, color: 'var(--tx3)', marginBottom: 6 }}>1. {t('settings.sttTest')}</div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <button onClick={startSttTest} disabled={recording}
                className={cn("text-xs px-4 py-2 rounded-lg font-medium flex items-center gap-2 disabled:opacity-50",
                  recording ? "bg-red-600 text-white animate-pulse" : "bg-blue-600 text-white hover:bg-blue-500")}>
                <Mic size={14} />
                {recording ? `${t('settings.recording')} ${countdown}${t('settings.sec')}` : t('settings.testStt')}
              </button>
              {peakLevel > 0 && !recording && (
                <div style={{ flex: 1, height: 6, background: 'var(--sf3)', borderRadius: 3, overflow: 'hidden' }}>
                  <div style={{ width: `${Math.min(peakLevel * 100, 100)}%`, height: '100%', background: peakLevel > 0.01 ? 'var(--gr)' : 'var(--rd)', borderRadius: 3, transition: 'width 0.3s' }} />
                </div>
              )}
            </div>
          </div>

          {/* Recognized text */}
          {recognizedText && (
            <div style={{ marginBottom: 14 }}>
              <div style={{ fontSize: 11, color: 'var(--tx3)', marginBottom: 4 }}>{t('settings.recognizedText')}</div>
              <div style={{
                padding: '10px 12px', background: 'var(--sf)', border: '1px solid var(--b)', borderRadius: 8,
                fontSize: 14, color: 'var(--tx)', minHeight: 36, lineHeight: 1.4,
              }}>
                {recognizedText}
              </div>
              <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                {recordedAudioB64 && (
                  <button onClick={playRecordedAudio}
                    className="text-xs px-3 py-1.5 rounded-lg bg-zinc-800 text-zinc-300 hover:bg-zinc-700 flex items-center gap-1">
                    <Volume2 size={12} /> {t('settings.playRecording')}
                  </button>
                )}
                {recognizedText !== '(...)' && recognizedText !== 'Connection error' && (
                  <>
                    <button onClick={() => speakText(recognizedText)} disabled={sendingToTts}
                      className="text-xs px-3 py-1.5 rounded-lg bg-zinc-800 text-zinc-300 hover:bg-zinc-700 disabled:opacity-50 flex items-center gap-1">
                      {sendingToTts ? <Loader2 size={12} className="animate-spin" /> : <Volume2 size={12} />}
                      TTS
                    </button>
                    <button onClick={sendRecognizedToAi} disabled={sendingToAi}
                      className="text-xs px-3 py-1.5 rounded-lg bg-purple-600 text-white hover:bg-purple-500 disabled:opacity-50 flex items-center gap-1">
                      {sendingToAi ? <Loader2 size={12} className="animate-spin" /> : <Bot size={12} />}
                      {t('settings.sendToAi')}
                    </button>
                  </>
                )}
              </div>
            </div>
          )}

          {/* Step 2: Manual text to AI */}
          <div style={{ marginBottom: 14 }}>
            <div style={{ fontSize: 11, color: 'var(--tx3)', marginBottom: 6 }}>2. {t('settings.manualAiQuery')}</div>
            <div style={{ display: 'flex', gap: 8 }}>
              <input type="text" value={manualText} onChange={e => setManualText(e.target.value)}
                placeholder={t('settings.aiTextPlaceholder')}
                onKeyDown={e => e.key === 'Enter' && sendManualToAi()}
                style={{
                  flex: 1, padding: '8px 10px', fontSize: 12,
                  background: 'var(--sf)', border: '1px solid var(--b)', borderRadius: 8, color: 'var(--tx)',
                }}
              />
              <button onClick={sendManualToAi} disabled={sendingToAi || !manualText.trim()}
                className="text-xs px-4 py-2 rounded-lg bg-purple-600 text-white hover:bg-purple-500 disabled:opacity-50 flex items-center gap-1">
                {sendingToAi ? <Loader2 size={12} className="animate-spin" /> : <Send size={12} />}
                {t('settings.sendToAi')}
              </button>
            </div>
          </div>

          {/* Step 3: AI Response */}
          {aiResponse && (
            <div style={{ marginBottom: 8 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                <Bot size={12} style={{ color: 'var(--ac)' }} />
                <span style={{ fontSize: 11, color: 'var(--tx3)' }}>
                  3. {t('settings.aiResponse')}
                  {aiProvider && <span style={{ color: 'var(--tx2)' }}> — {aiProvider}{aiModel ? ` / ${aiModel}` : ''}</span>}
                </span>
              </div>
              <div style={{
                padding: '10px 12px', background: 'var(--sf)', border: '1px solid var(--ac)',
                borderRadius: 8, fontSize: 13, color: 'var(--tx)', lineHeight: 1.5, whiteSpace: 'pre-wrap',
              }}>
                {aiResponse}
              </div>
              {!aiResponse.startsWith('Error:') && aiResponse !== '(empty)' && (
                <div style={{ marginTop: 8 }}>
                  <button onClick={speakAiResponse} disabled={speakingAiResponse || sendingToTts}
                    className="text-xs px-3 py-1.5 rounded-lg bg-emerald-600 text-white hover:bg-emerald-500 disabled:opacity-50 flex items-center gap-1">
                    {speakingAiResponse ? <Loader2 size={12} className="animate-spin" /> : <Volume2 size={12} />}
                    {t('settings.speakResponse')}
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Catalog toggle */}
      <button onClick={showCatalog ? () => setShowCatalog(false) : loadCatalog}
        className="text-xs text-blue-400 hover:text-blue-300 mb-3">
        {showCatalog ? t('settings.hideCatalog') : t('settings.showCatalog')}
      </button>

      {/* Catalog */}
      {showCatalog && (
        <div>
          {/* Filters */}
          <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
            <div style={{ position: 'relative', flex: 1 }}>
              <Search size={12} style={{ position: 'absolute', left: 8, top: '50%', transform: 'translateY(-50%)', color: 'var(--tx3)' }} />
              <input
                type="text" value={search} onChange={e => setSearch(e.target.value)}
                placeholder={t('settings.searchModels')}
                style={{
                  width: '100%', paddingLeft: 28, paddingRight: 8, paddingTop: 6, paddingBottom: 6,
                  fontSize: 12, background: 'var(--sf2)', border: '1px solid var(--b2)', borderRadius: 8, color: 'var(--tx)',
                }}
              />
            </div>
            <select value={langFilter} onChange={e => setLangFilter(e.target.value)}
              style={{ fontSize: 12, padding: '6px 10px', background: 'var(--sf2)', border: '1px solid var(--b2)', borderRadius: 8, color: 'var(--tx)' }}>
              <option value="">{t('settings.allLanguages')}</option>
              {languages.map(l => <option key={l} value={l}>{l}</option>)}
            </select>
          </div>

          {loadingCatalog ? (
            <div className="flex items-center gap-2 text-xs text-zinc-400 py-4">
              <Loader2 size={14} className="animate-spin" /> {t('settings.fetchingCatalog')}
            </div>
          ) : filteredCatalog.length === 0 ? (
            <div className="text-xs text-zinc-500 py-4">{t('settings.noModelsFound')}</div>
          ) : (
            <div className="space-y-2" style={{ maxHeight: 400, overflowY: 'auto' }}>
              {filteredCatalog.map(m => (
                <div key={m.id} className="p-3 rounded-lg border border-zinc-800 bg-zinc-950 flex items-center justify-between">
                  <div>
                    <div className="text-sm font-medium">{m.name}</div>
                    <div className="text-xs text-zinc-500">
                      {m.lang && <span>{m.lang.toUpperCase()}</span>}
                      {m.size_mb ? <span> · {m.size_mb} MB</span> : null}
                      {m.notes && <span> · {m.notes}</span>}
                    </div>
                  </div>
                  <button onClick={() => downloadModel(m.id)} disabled={downloading !== null}
                    className="text-xs px-3 py-1.5 rounded-lg bg-zinc-800 text-zinc-300 hover:bg-zinc-700 flex items-center gap-1 disabled:opacity-50">
                    {downloading === m.id ? <Loader2 size={12} className="animate-spin" /> : <Download size={12} />}
                    {downloading === m.id ? t('settings.downloading') : t('settings.download')}
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
