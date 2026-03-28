import { useState, useEffect, useCallback } from 'react';
import { RefreshCw, Download, Trash2, Check, Search, Play, Loader2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { cn } from '../../lib/utils';

interface PiperVoice {
  id: string;
  name: string;
  language?: string;
  language_code?: string;
  language_name?: string;
  country?: string;
  quality?: string;
  num_speakers?: number;
  size_bytes?: number;
  size_mb?: number;
  installed?: boolean;
  active?: boolean;
}

export default function PiperSection() {
  const { t } = useTranslation();
  const [engineStatus, setEngineStatus] = useState<{ installed: boolean; version: string | null }>({ installed: false, version: null });
  const [installing, setInstalling] = useState(false);
  const [installAction, setInstallAction] = useState<string>('');
  const [installedVoices, setInstalledVoices] = useState<PiperVoice[]>([]);
  const [catalogVoices, setCatalogVoices] = useState<PiperVoice[]>([]);
  const [activeVoice, setActiveVoice] = useState('');
  const [langFilter, setLangFilter] = useState('');
  const [qualityFilter, setQualityFilter] = useState('');
  const [search, setSearch] = useState('');
  const [showCatalog, setShowCatalog] = useState(false);
  const [loadingCatalog, setLoadingCatalog] = useState(false);
  const [downloading, setDownloading] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [previewingVoice, setPreviewingVoice] = useState<string | null>(null);

  // TTS settings
  const [ttsSettings, setTtsSettings] = useState({
    length_scale: 1.0, noise_scale: 0.667, noise_w_scale: 0.8,
    sentence_silence: 0.2, volume: 1.0, speaker: 0,
  });
  const [numSpeakers, setNumSpeakers] = useState(1);
  const [savingSettings, setSavingSettings] = useState(false);
  const [testText, setTestText] = useState('');

  const checkStatus = useCallback(async () => {
    try {
      const res = await fetch('/api/ui/setup/piper/status').then(r => r.json());
      setEngineStatus(res);
    } catch { /* ignore */ }
  }, []);

  const fetchInstalled = useCallback(async () => {
    try {
      const res = await fetch('/api/ui/setup/tts/voices').then(r => r.json());
      setInstalledVoices(res.voices || []);
      setActiveVoice(res.active || '');
    } catch { /* ignore */ }
  }, []);

  const fetchSettings = useCallback(async () => {
    try {
      const res = await fetch('/api/ui/setup/tts/settings').then(r => r.json());
      setTtsSettings({
        length_scale: res.length_scale ?? 1.0,
        noise_scale: res.noise_scale ?? 0.667,
        noise_w_scale: res.noise_w_scale ?? 0.8,
        sentence_silence: res.sentence_silence ?? 0.2,
        volume: res.volume ?? 1.0,
        speaker: res.speaker ?? 0,
      });
      setNumSpeakers(res.num_speakers ?? 1);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    checkStatus();
    fetchInstalled();
    fetchSettings();
  }, [checkStatus, fetchInstalled, fetchSettings]);

  const handleInstall = async () => {
    setInstalling(true);
    setInstallAction('install');
    try {
      await fetch('/api/ui/setup/piper/install', { method: 'POST' });
      const poll = setInterval(async () => {
        const res = await fetch('/api/ui/setup/piper/install-progress').then(r => r.json());
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
      await fetch('/api/ui/setup/piper/uninstall', { method: 'POST' });
      const poll = setInterval(async () => {
        const res = await fetch('/api/ui/setup/piper/install-progress').then(r => r.json());
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
      const res = await fetch('/api/ui/setup/tts/catalog').then(r => r.json());
      setCatalogVoices(res.voices || []);
    } catch { /* ignore */ }
    setLoadingCatalog(false);
  };

  const selectVoice = async (voiceId: string) => {
    setSaving(true);
    try {
      await fetch('/api/ui/setup/tts/select', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ voice: voiceId }),
      });
      setActiveVoice(voiceId);
    } catch { /* ignore */ }
    setSaving(false);
  };

  const downloadVoice = async (voiceId: string) => {
    setDownloading(voiceId);
    try {
      await fetch('/api/ui/setup/tts/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ voice: voiceId }),
      });
      const poll = setInterval(async () => {
        const res = await fetch(`/api/ui/setup/tts/download-progress/${voiceId}`).then(r => r.json());
        if (!res.running) {
          clearInterval(poll);
          setDownloading(null);
          fetchInstalled();
        }
      }, 3000);
    } catch { setDownloading(null); }
  };

  const deleteVoice = async (voiceId: string) => {
    try {
      await fetch('/api/ui/setup/tts/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ voice: voiceId }),
      });
      fetchInstalled();
    } catch { /* ignore */ }
  };

  const previewVoice = async (voiceId: string) => {
    if (previewingVoice) return;
    setPreviewingVoice(voiceId);
    try {
      const res = await fetch('/api/ui/setup/tts/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: 'Hello, I am your voice assistant.', voice: voiceId }),
      });
      if (res.ok) {
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const audio = new Audio(url);
        audio.onended = () => { URL.revokeObjectURL(url); setPreviewingVoice(null); };
        audio.onerror = () => { URL.revokeObjectURL(url); setPreviewingVoice(null); };
        await audio.play();
      } else { setPreviewingVoice(null); }
    } catch { setPreviewingVoice(null); }
  };

  const saveSettings = async () => {
    setSavingSettings(true);
    try {
      await fetch('/api/ui/setup/tts/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(ttsSettings),
      });
    } catch { /* ignore */ }
    setSavingSettings(false);
  };

  const testWithSettings = async () => {
    const text = testText.trim() || 'Привіт, я ваш голосовий асистент. Як справи?';
    setPreviewingVoice('__test__');
    try {
      // Save settings first so backend uses them
      await fetch('/api/ui/setup/tts/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(ttsSettings),
      });
      const res = await fetch('/api/ui/setup/tts/speak', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, output_device: 'default' }),
      });
      if (res.ok) {
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const audio = new Audio(url);
        audio.onended = () => { URL.revokeObjectURL(url); setPreviewingVoice(null); };
        audio.onerror = () => { URL.revokeObjectURL(url); setPreviewingVoice(null); };
        await audio.play();
      } else { setPreviewingVoice(null); }
    } catch { setPreviewingVoice(null); }
  };

  const updateSetting = (key: string, value: number) => {
    setTtsSettings(prev => ({ ...prev, [key]: value }));
  };

  const languages = [...new Set(catalogVoices.map(v => v.language).filter(Boolean))].sort();
  const qualities = [...new Set(catalogVoices.map(v => v.quality).filter(Boolean))].sort();
  const installedIds = new Set(installedVoices.map(v => v.id));

  const filteredCatalog = catalogVoices.filter(v => {
    if (installedIds.has(v.id)) return false;
    if (langFilter && v.language !== langFilter) return false;
    if (qualityFilter && v.quality !== qualityFilter) return false;
    if (search && !v.id.toLowerCase().includes(search.toLowerCase()) && !v.name.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  const formatSize = (bytes?: number) => {
    if (!bytes) return '';
    if (bytes > 1024 * 1024) return `${Math.round(bytes / (1024 * 1024))} MB`;
    return `${Math.round(bytes / 1024)} KB`;
  };

  return (
    <div style={{ background: 'var(--sf)', border: '1px solid var(--b)', borderRadius: 12, padding: 20 }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
        <div>
          <h4 style={{ fontWeight: 600, fontSize: 15, color: 'var(--tx)' }}>{t('settings.ttsVoice')}</h4>
          <p style={{ fontSize: 12, color: 'var(--tx2)', marginTop: 2 }}>Piper TTS</p>
        </div>
        <span className={cn("text-xs px-2 py-1 rounded-md font-medium",
          engineStatus.installed ? "text-emerald-500 bg-emerald-500/10" : "text-red-400 bg-red-500/10")}>
          {engineStatus.installed ? `${t('settings.installed')} v${engineStatus.version}` : t('settings.notInstalled')}
        </span>
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

      {/* Installed voices */}
      {installedVoices.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 12, fontWeight: 500, color: 'var(--tx2)', marginBottom: 8 }}>{t('settings.installedVoices')}</div>
          <div className="grid grid-cols-2 gap-3">
            {installedVoices.map(v => (
              <div key={v.id} className={cn("p-3 rounded-lg border transition-all",
                v.id === activeVoice ? "border-emerald-500 bg-emerald-500/10" : "border-zinc-800 bg-zinc-950")}>
                <div className="flex items-center justify-between mb-1">
                  <button onClick={() => selectVoice(v.id)} disabled={saving} className="text-left flex-1">
                    <div className="text-sm font-medium">{v.name || v.id}</div>
                    <div className="text-xs text-zinc-500">
                      {v.language && <span>{v.language.toUpperCase()}</span>}
                      {v.size_mb ? <span> · {v.size_mb} MB</span> : null}
                    </div>
                  </button>
                  {v.id === activeVoice && <Check size={14} className="text-emerald-500 shrink-0" />}
                </div>
                <div className="flex items-center gap-2 mt-2">
                  {engineStatus.installed && (
                    <button onClick={() => previewVoice(v.id)} disabled={previewingVoice !== null}
                      className="text-xs text-zinc-400 hover:text-emerald-400 flex items-center gap-1 disabled:opacity-50">
                      <Play size={11} className={previewingVoice === v.id ? 'animate-pulse text-emerald-500' : ''} />
                      {previewingVoice === v.id ? t('settings.playing') : t('settings.preview')}
                    </button>
                  )}
                  <button onClick={() => deleteVoice(v.id)} disabled={v.id === activeVoice}
                    className="text-xs text-red-400 hover:text-red-300 flex items-center gap-1 disabled:opacity-30">
                    <Trash2 size={11} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* TTS Settings */}
      {engineStatus.installed && installedVoices.length > 0 && (
        <div style={{ background: 'var(--sf2)', border: '1px solid var(--b2)', borderRadius: 10, padding: 16, marginBottom: 16 }}>
          <div style={{ fontSize: 12, fontWeight: 500, color: 'var(--tx2)', marginBottom: 12 }}>{t('settings.ttsSettings')}</div>

          {/* Length Scale (speed) */}
          <div style={{ marginBottom: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
              <span style={{ fontSize: 11, color: 'var(--tx2)' }}>{t('settings.ttsSpeed')}</span>
              <span style={{ fontSize: 11, color: 'var(--tx)', fontFamily: 'var(--font-mono)' }}>{ttsSettings.length_scale.toFixed(2)}</span>
            </div>
            <input type="range" min="0.5" max="2.0" step="0.05" value={ttsSettings.length_scale}
              onChange={e => updateSetting('length_scale', parseFloat(e.target.value))}
              style={{ width: '100%', height: 6, cursor: 'pointer' }} />
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--tx3)' }}>
              <span>{t('settings.fast')}</span><span>{t('settings.slow')}</span>
            </div>
          </div>

          {/* Noise Scale (intonation) */}
          <div style={{ marginBottom: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
              <span style={{ fontSize: 11, color: 'var(--tx2)' }}>{t('settings.ttsIntonation')}</span>
              <span style={{ fontSize: 11, color: 'var(--tx)', fontFamily: 'var(--font-mono)' }}>{ttsSettings.noise_scale.toFixed(3)}</span>
            </div>
            <input type="range" min="0" max="1" step="0.01" value={ttsSettings.noise_scale}
              onChange={e => updateSetting('noise_scale', parseFloat(e.target.value))}
              style={{ width: '100%', height: 6, cursor: 'pointer' }} />
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--tx3)' }}>
              <span>{t('settings.monotone')}</span><span>{t('settings.expressive')}</span>
            </div>
          </div>

          {/* Noise W Scale (phoneme width) */}
          <div style={{ marginBottom: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
              <span style={{ fontSize: 11, color: 'var(--tx2)' }}>{t('settings.ttsPhonemeWidth')}</span>
              <span style={{ fontSize: 11, color: 'var(--tx)', fontFamily: 'var(--font-mono)' }}>{ttsSettings.noise_w_scale.toFixed(3)}</span>
            </div>
            <input type="range" min="0" max="1" step="0.01" value={ttsSettings.noise_w_scale}
              onChange={e => updateSetting('noise_w_scale', parseFloat(e.target.value))}
              style={{ width: '100%', height: 6, cursor: 'pointer' }} />
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--tx3)' }}>
              <span>{t('settings.stable')}</span><span>{t('settings.varied')}</span>
            </div>
          </div>

          {/* Sentence Silence */}
          <div style={{ marginBottom: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
              <span style={{ fontSize: 11, color: 'var(--tx2)' }}>{t('settings.ttsSentencePause')}</span>
              <span style={{ fontSize: 11, color: 'var(--tx)', fontFamily: 'var(--font-mono)' }}>{ttsSettings.sentence_silence.toFixed(2)}s</span>
            </div>
            <input type="range" min="0" max="2" step="0.05" value={ttsSettings.sentence_silence}
              onChange={e => updateSetting('sentence_silence', parseFloat(e.target.value))}
              style={{ width: '100%', height: 6, cursor: 'pointer' }} />
          </div>

          {/* Volume */}
          <div style={{ marginBottom: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
              <span style={{ fontSize: 11, color: 'var(--tx2)' }}>{t('settings.ttsVolume')}</span>
              <span style={{ fontSize: 11, color: 'var(--tx)', fontFamily: 'var(--font-mono)' }}>{ttsSettings.volume.toFixed(2)}</span>
            </div>
            <input type="range" min="0.1" max="3.0" step="0.05" value={ttsSettings.volume}
              onChange={e => updateSetting('volume', parseFloat(e.target.value))}
              style={{ width: '100%', height: 6, cursor: 'pointer' }} />
          </div>

          {/* Speaker (only for multi-speaker models) */}
          {numSpeakers > 1 && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                <span style={{ fontSize: 11, color: 'var(--tx2)' }}>{t('settings.ttsSpeaker')}</span>
                <span style={{ fontSize: 11, color: 'var(--tx)', fontFamily: 'var(--font-mono)' }}>{ttsSettings.speaker}</span>
              </div>
              <input type="range" min="0" max={numSpeakers - 1} step="1" value={ttsSettings.speaker}
                onChange={e => updateSetting('speaker', parseInt(e.target.value))}
                style={{ width: '100%', height: 6, cursor: 'pointer' }} />
            </div>
          )}

          {/* Test text + buttons */}
          <div style={{ marginBottom: 10 }}>
            <input type="text" value={testText} onChange={e => setTestText(e.target.value)}
              placeholder={t('settings.ttsTestPlaceholder')}
              style={{
                width: '100%', padding: '8px 10px', fontSize: 12,
                background: 'var(--sf)', border: '1px solid var(--b)', borderRadius: 8, color: 'var(--tx)',
              }} />
          </div>

          <div style={{ display: 'flex', gap: 8 }}>
            <button onClick={testWithSettings} disabled={previewingVoice !== null}
              className="text-xs px-3 py-1.5 rounded-lg bg-blue-600 text-white hover:bg-blue-500 disabled:opacity-50 flex items-center gap-1">
              <Play size={12} className={previewingVoice === '__test__' ? 'animate-pulse' : ''} />
              {previewingVoice === '__test__' ? t('settings.playing') : t('settings.testVoice')}
            </button>
            <button onClick={saveSettings} disabled={savingSettings}
              className="text-xs px-3 py-1.5 rounded-lg bg-emerald-600 text-white hover:bg-emerald-500 disabled:opacity-50 flex items-center gap-1">
              {savingSettings ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
              {t('common.save')}
            </button>
          </div>
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
          <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
            <div style={{ position: 'relative', flex: 1, minWidth: 150 }}>
              <Search size={12} style={{ position: 'absolute', left: 8, top: '50%', transform: 'translateY(-50%)', color: 'var(--tx3)' }} />
              <input type="text" value={search} onChange={e => setSearch(e.target.value)}
                placeholder={t('settings.searchVoices')}
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
            <select value={qualityFilter} onChange={e => setQualityFilter(e.target.value)}
              style={{ fontSize: 12, padding: '6px 10px', background: 'var(--sf2)', border: '1px solid var(--b2)', borderRadius: 8, color: 'var(--tx)' }}>
              <option value="">{t('settings.allQualities')}</option>
              {qualities.map(q => <option key={q} value={q}>{q}</option>)}
            </select>
          </div>

          {loadingCatalog ? (
            <div className="flex items-center gap-2 text-xs text-zinc-400 py-4">
              <Loader2 size={14} className="animate-spin" /> {t('settings.fetchingCatalog')}
            </div>
          ) : filteredCatalog.length === 0 ? (
            <div className="text-xs text-zinc-500 py-4">{t('settings.noVoicesFound')}</div>
          ) : (
            <div className="space-y-2" style={{ maxHeight: 400, overflowY: 'auto' }}>
              {filteredCatalog.map(v => (
                <div key={v.id} className="p-3 rounded-lg border border-zinc-800 bg-zinc-950 flex items-center justify-between">
                  <div>
                    <div className="text-sm font-medium">{v.id}</div>
                    <div className="text-xs text-zinc-500">
                      {v.language_name || v.language || ''}
                      {v.country && ` (${v.country})`}
                      {v.quality && ` · ${v.quality}`}
                      {v.size_bytes ? ` · ${formatSize(v.size_bytes)}` : ''}
                    </div>
                  </div>
                  <button onClick={() => downloadVoice(v.id)} disabled={downloading !== null}
                    className="text-xs px-3 py-1.5 rounded-lg bg-zinc-800 text-zinc-300 hover:bg-zinc-700 flex items-center gap-1 disabled:opacity-50">
                    {downloading === v.id ? <Loader2 size={12} className="animate-spin" /> : <Download size={12} />}
                    {downloading === v.id ? t('settings.downloading') : t('settings.download')}
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
