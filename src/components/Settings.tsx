import { useState, useEffect, useCallback } from 'react';
import { Routes, Route, Link, useLocation } from 'react-router-dom';
import { Mic, Volume2, Network, Users, Activity, Shield, RefreshCw, Play, Download, Check, Wifi, Lock, Globe, Cpu, Palette } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { cn } from '../lib/utils';
import { useStore } from '../store/useStore';

export default function Settings() {
  const { t } = useTranslation();
  const location = useLocation();

  const tabs = [
    { id: 'appearance', label: t('settings.appearance'), icon: Palette, path: '/settings/appearance' },
    { id: 'voice', label: t('settings.voiceAndLlm'), icon: Mic, path: '/settings/voice' },
    { id: 'audio', label: t('settings.audio'), icon: Volume2, path: '/settings/audio' },
    { id: 'network', label: t('settings.networkAndVpn'), icon: Network, path: '/settings/network' },
    { id: 'users', label: t('settings.users'), icon: Users, path: '/settings/users' },
    { id: 'system', label: t('settings.system'), icon: Activity, path: '/settings/system' },
    { id: 'security', label: t('settings.security'), icon: Shield, path: '/settings/security' },
    { id: 'system-modules', label: t('settings.systemModules'), icon: Cpu, path: '/settings/system-modules' },
  ];

  const activeId =
    tabs.find(tab => location.pathname === tab.path)?.id ??
    (location.pathname === '/settings' ? 'appearance' : '');

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* Horizontal tab bar */}
      <div style={{ display: 'flex', borderBottom: '1px solid var(--b)', overflowX: 'auto', flexShrink: 0, scrollbarWidth: 'none' }}>
        {tabs.map((tab) => {
          const isActive = tab.id === activeId;
          return (
            <Link
              key={tab.id}
              to={tab.path}
              style={{
                display: 'flex', alignItems: 'center', gap: 6,
                padding: '10px 16px',
                fontSize: 12, fontWeight: isActive ? 600 : 400,
                color: isActive ? 'var(--tx)' : 'var(--tx3)',
                borderBottom: `2px solid ${isActive ? 'var(--ac)' : 'transparent'}`,
                whiteSpace: 'nowrap', cursor: 'pointer',
                transition: 'color .15s', textDecoration: 'none',
              }}
            >
              <tab.icon size={13} />
              {tab.label}
            </Link>
          );
        })}
      </div>

      {/* Settings content */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '24px' }}>
        <Routes>
          <Route path="/" element={<AppearanceSettings />} />
          <Route path="/appearance" element={<AppearanceSettings />} />
          <Route path="/voice" element={<VoiceSettings />} />
          <Route path="/audio" element={<AudioSettings />} />
          <Route path="/network" element={<NetworkSettings />} />
          <Route path="/system" element={<SystemSettings />} />
          <Route path="/system-modules" element={<SystemModulesSettings />} />
          <Route path="*" element={<div className="text-zinc-400">{t('common.inDevelopment')}</div>} />
        </Routes>
      </div>
    </div>
  );
}

// ================================================================ //
//  Appearance Settings                                                //
// ================================================================ //

import type { ThemeMode } from '../store/useStore';

function AppearanceSettings() {
  const { t } = useTranslation();
  const theme = useStore(s => s.theme);
  const setTheme = useStore(s => s.setTheme);
  const selectedLanguage = useStore(s => s.selectedLanguage);
  const setSelectedLanguage = useStore(s => s.setSelectedLanguage);

  const themeOptions: { value: ThemeMode; label: string; desc: string; icon: string }[] = [
    { value: 'auto', label: t('settings.themeAuto'), desc: t('settings.themeAutoDesc'), icon: '🖥️' },
    { value: 'dark', label: t('settings.themeDark'), desc: t('settings.themeDarkDesc'), icon: '🌙' },
    { value: 'light', label: t('settings.themeLight'), desc: t('settings.themeLightDesc'), icon: '☀️' },
  ];

  const languages = [
    { code: 'en', label: 'English', flag: '🇬🇧' },
    { code: 'uk', label: 'Українська', flag: '🇺🇦' },
  ];

  return (
    <div className="space-y-8">
      <div>
        <h3 style={{ fontSize: 20, fontWeight: 600, marginBottom: 4, color: 'var(--tx)' }}>{t('settings.appearance')}</h3>
        <p style={{ fontSize: 13, color: 'var(--tx2)' }}>{t('settings.appearanceDesc')}</p>
      </div>

      {/* Theme selector */}
      <div style={{ background: 'var(--sf)', border: '1px solid var(--b)', borderRadius: 12, padding: 20 }}>
        <h4 style={{ fontWeight: 500, marginBottom: 16, color: 'var(--tx)' }}>{t('settings.theme')}</h4>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12 }}>
          {themeOptions.map(opt => {
            const isActive = theme === opt.value;
            return (
              <button
                key={opt.value}
                onClick={() => setTheme(opt.value)}
                style={{
                  padding: '16px 12px',
                  borderRadius: 10,
                  border: `2px solid ${isActive ? 'var(--ac)' : 'var(--b)'}`,
                  background: isActive ? 'rgba(79,140,247,.08)' : 'var(--sf2)',
                  cursor: 'pointer',
                  display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8,
                  transition: 'all .15s',
                }}
              >
                <span style={{ fontSize: 28 }}>{opt.icon}</span>
                <span style={{ fontSize: 13, fontWeight: 600, color: isActive ? 'var(--ac)' : 'var(--tx)' }}>{opt.label}</span>
                <span style={{ fontSize: 11, color: 'var(--tx3)', textAlign: 'center' }}>{opt.desc}</span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Language selector */}
      <div style={{ background: 'var(--sf)', border: '1px solid var(--b)', borderRadius: 12, padding: 20 }}>
        <h4 style={{ fontWeight: 500, marginBottom: 16, color: 'var(--tx)' }}>{t('settings.language')}</h4>
        <div style={{ display: 'flex', gap: 12 }}>
          {languages.map(lang => {
            const isActive = selectedLanguage === lang.code;
            return (
              <button
                key={lang.code}
                onClick={() => setSelectedLanguage(lang.code)}
                style={{
                  padding: '12px 24px',
                  borderRadius: 10,
                  border: `2px solid ${isActive ? 'var(--ac)' : 'var(--b)'}`,
                  background: isActive ? 'rgba(79,140,247,.08)' : 'var(--sf2)',
                  cursor: 'pointer',
                  display: 'flex', alignItems: 'center', gap: 10,
                  transition: 'all .15s',
                }}
              >
                <span style={{ fontSize: 22 }}>{lang.flag}</span>
                <span style={{ fontSize: 13, fontWeight: 500, color: isActive ? 'var(--ac)' : 'var(--tx)' }}>{lang.label}</span>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ================================================================ //
//  Voice & LLM Settings                                              //
// ================================================================ //

interface LlmModel {
  id: string;
  display_name: string;
  size_gb: number;
  ram_required_gb: number;
  installed: boolean;
  active: boolean;
}

function VoiceSettings() {
  const { t } = useTranslation();
  const [sttModels, setSttModels] = useState<any[]>([]);
  const [ttsVoices, setTtsVoices] = useState<any[]>([]);
  const [llmModels, setLlmModels] = useState<LlmModel[]>([]);
  const [llmActive, setLlmActive] = useState<string | null>(null);
  const [llmAvailable, setLlmAvailable] = useState(false);
  const [ramAvailableGb, setRamAvailableGb] = useState(0);
  const [activeStt, setActiveStt] = useState('');
  const [activeTts, setActiveTts] = useState('');
  const [saving, setSaving] = useState<string | null>(null);
  const [downloading, setDownloading] = useState<string | null>(null);
  const [previewingVoice, setPreviewingVoice] = useState<string | null>(null);

  const fetchAll = useCallback(async () => {
    try {
      const [sttRes, ttsRes, llmRes] = await Promise.all([
        fetch('/api/ui/setup/stt/models').then(r => r.json()),
        fetch('/api/ui/setup/tts/voices').then(r => r.json()),
        fetch('/api/ui/setup/llm/models').then(r => r.json()),
      ]);
      setSttModels(sttRes.models || []);
      setActiveStt(sttRes.active || '');
      setTtsVoices(ttsRes.voices || []);
      setActiveTts(ttsRes.active || '');
      setLlmModels(llmRes.models || []);
      setLlmActive(llmRes.active || null);
      setLlmAvailable(llmRes.ollama_available || false);
      setRamAvailableGb(llmRes.ram_available_gb || 0);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  const selectStt = async (model: string) => {
    setSaving('stt');
    try {
      await fetch('/api/ui/setup/stt/select', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model }),
      });
      setActiveStt(model);
    } catch { /* ignore */ }
    setSaving(null);
  };

  const selectTts = async (voice: string) => {
    setSaving('tts');
    try {
      await fetch('/api/ui/setup/tts/select', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ voice }),
      });
      setActiveTts(voice);
    } catch { /* ignore */ }
    setSaving(null);
  };

  const selectLlm = async (model: string) => {
    setSaving('llm');
    try {
      await fetch('/api/ui/setup/llm/select', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model }),
      });
      setLlmActive(model);
    } catch { /* ignore */ }
    setSaving(null);
  };

  const downloadLlm = async (model: string) => {
    setDownloading(model);
    try {
      await fetch('/api/ui/setup/llm/download', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model }),
      });
      // Re-fetch after a delay (download is async)
      setTimeout(fetchAll, 5000);
    } catch { /* ignore */ }
    setDownloading(null);
  };

  const previewVoice = async (voiceId: string) => {
    if (previewingVoice) return;
    setPreviewingVoice(voiceId);
    try {
      const res = await fetch('/api/ui/setup/tts/preview', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
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

  return (
    <div className="space-y-8">
      <div>
        <h3 className="text-xl font-semibold mb-1">{t('settings.voiceAssistant')}</h3>
        <p className="text-sm text-zinc-400">{t('settings.voiceAssistantDesc')}</p>
      </div>

      {/* STT Model Selection */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
        <div className="flex items-center justify-between mb-4">
          <h4 className="font-medium">{t('settings.sttModel')}</h4>
          <button onClick={fetchAll} className="text-xs text-zinc-400 hover:text-zinc-200 flex items-center gap-1"><RefreshCw size={12} /> {t('common.refresh')}</button>
        </div>
        <div className="space-y-2">
          {sttModels.map(m => (
            <button key={m.id} onClick={() => selectStt(m.id)} disabled={saving === 'stt' || !m.fits_ram}
              className={cn("w-full p-3 rounded-lg border text-left flex items-center justify-between transition-all text-sm",
                m.id === activeStt ? "border-emerald-500 bg-emerald-500/10" : "border-zinc-800 bg-zinc-950 hover:border-zinc-700",
                !m.fits_ram && "opacity-50 cursor-not-allowed"
              )}>
              <div className="flex items-center gap-3">
                <span className="font-medium">{m.name}</span>
                <span className="text-xs text-zinc-500">~{m.ram_mb} MB RAM</span>
                {m.installed && <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-500">{t('settings.installed')}</span>}
              </div>
              {m.id === activeStt && <Check size={16} className="text-emerald-500" />}
            </button>
          ))}
        </div>
      </div>

      {/* TTS Voice Selection */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
        <h4 className="font-medium mb-4">{t('settings.ttsVoice')}</h4>
        <div className="grid grid-cols-2 gap-3">
          {ttsVoices.map(v => (
            <div key={v.id} className={cn("p-3 rounded-lg border transition-all",
              v.id === activeTts ? "border-emerald-500 bg-emerald-500/10" : "border-zinc-800 bg-zinc-950 hover:border-zinc-700")}>
              <button onClick={() => selectTts(v.id)} disabled={saving === 'tts'} className="w-full text-left">
                <div className="text-sm font-medium">{v.name}</div>
                <div className="text-xs text-zinc-500">{v.language.toUpperCase()} · {v.gender}</div>
              </button>
              <button onClick={() => previewVoice(v.id)} disabled={previewingVoice !== null}
                className="mt-1.5 text-xs text-zinc-400 hover:text-emerald-400 flex items-center gap-1 disabled:opacity-50">
                <Play size={11} className={previewingVoice === v.id ? 'animate-pulse text-emerald-500' : ''} />
                {previewingVoice === v.id ? t('settings.playing') : t('settings.preview')}
              </button>
            </div>
          ))}
        </div>
      </div>

      {/* LLM Model Selection */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h4 className="font-medium">{t('settings.llmRouter')}</h4>
            <div className="text-xs text-zinc-500 mt-1">{t('settings.localLlmDesc')}</div>
          </div>
          <span className={cn("text-xs px-2 py-1 rounded-md font-medium",
            llmAvailable ? "text-emerald-500 bg-emerald-500/10" : "text-red-400 bg-red-500/10")}>
            {llmAvailable ? t('settings.llmActive') : t('settings.llmUnavailable')}
          </span>
        </div>
        {ramAvailableGb > 0 && (
          <div className="text-xs text-zinc-500 mb-3">RAM {t('settings.available')}: {ramAvailableGb} GB</div>
        )}
        <div className="space-y-2">
          {llmModels.map(m => (
            <div key={m.id} className={cn("p-3 rounded-lg border flex items-center justify-between transition-all",
              m.id === llmActive ? "border-emerald-500 bg-emerald-500/10" : "border-zinc-800 bg-zinc-950")}>
              <div>
                <div className="text-sm font-medium">{m.display_name}</div>
                <div className="text-xs text-zinc-500">{m.size_gb} GB · {t('settings.ramRequired')}: {m.ram_required_gb} GB</div>
              </div>
              <div className="flex items-center gap-2">
                {m.installed ? (
                  <button onClick={() => selectLlm(m.id)} disabled={m.id === llmActive || saving === 'llm'}
                    className={cn("text-xs px-3 py-1.5 rounded-lg transition-colors",
                      m.id === llmActive ? "bg-emerald-500/20 text-emerald-500" : "bg-zinc-800 text-zinc-300 hover:bg-zinc-700")}>
                    {m.id === llmActive ? t('common.active') : t('settings.activate')}
                  </button>
                ) : (
                  <button onClick={() => downloadLlm(m.id)} disabled={downloading !== null}
                    className="text-xs px-3 py-1.5 rounded-lg bg-zinc-800 text-zinc-300 hover:bg-zinc-700 flex items-center gap-1 disabled:opacity-50">
                    <Download size={12} className={downloading === m.id ? 'animate-bounce' : ''} />
                    {downloading === m.id ? t('settings.downloading') : t('settings.download')}
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ================================================================ //
//  Audio Settings                                                     //
// ================================================================ //

function AudioSettings() {
  const { t } = useTranslation();
  const [inputs, setInputs] = useState<any[]>([]);
  const [outputs, setOutputs] = useState<any[]>([]);
  const [selectedInput, setSelectedInput] = useState('');
  const [selectedOutput, setSelectedOutput] = useState('');

  useEffect(() => {
    fetch('/api/ui/setup/audio/devices').then(r => r.json()).then(data => {
      setInputs(data.inputs || []);
      setOutputs(data.outputs || []);
      if (data.inputs?.length) setSelectedInput(data.inputs[0].id);
      if (data.outputs?.length) setSelectedOutput(data.outputs[0].id);
    }).catch(() => { });
  }, []);

  const saveAudio = async () => {
    await fetch('/api/ui/setup/audio/select', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ input: selectedInput, output: selectedOutput }),
    });
  };

  return (
    <div className="space-y-8">
      <div>
        <h3 className="text-xl font-semibold mb-1">{t('settings.audioSubsystem')}</h3>
        <p className="text-sm text-zinc-400">{t('settings.audioSubsystemDesc')}</p>
      </div>

      <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
        <h4 className="font-medium mb-4">{t('settings.microphone')}</h4>
        <select value={selectedInput} onChange={(e) => { setSelectedInput(e.target.value); }}
          className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-emerald-500">
          {inputs.length > 0 ? inputs.map(d => (
            <option key={d.id} value={d.id}>{d.name} ({d.type})</option>
          )) : <option>{t('settings.noDevicesFound')}</option>}
        </select>
      </div>

      <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
        <h4 className="font-medium mb-4">{t('settings.speaker')}</h4>
        <select value={selectedOutput} onChange={(e) => { setSelectedOutput(e.target.value); }}
          className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-emerald-500">
          {outputs.length > 0 ? outputs.map(d => (
            <option key={d.id} value={d.id}>{d.name} ({d.type})</option>
          )) : <option>{t('settings.noDevicesFound')}</option>}
        </select>
      </div>

      <button onClick={saveAudio} className="px-4 py-2 rounded-lg bg-emerald-500 text-zinc-950 text-sm font-medium hover:bg-emerald-400 transition-colors">
        {t('common.save')}
      </button>
    </div>
  );
}

// ================================================================ //
//  Network Settings                                                   //
// ================================================================ //

function NetworkSettings() {
  const { t } = useTranslation();
  const [netStatus, setNetStatus] = useState<any>(null);
  const [wifiNetworks, setWifiNetworks] = useState<any[]>([]);
  const [scanning, setScanning] = useState(false);
  const [connecting, setConnecting] = useState<string | null>(null);
  const [password, setPassword] = useState('');
  const [selectedSsid, setSelectedSsid] = useState('');
  const [connectError, setConnectError] = useState('');

  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch('/api/ui/setup/network/status');
      setNetStatus(await res.json());
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { fetchStatus(); }, [fetchStatus]);

  const scanWifi = async () => {
    setScanning(true);
    try {
      const res = await fetch('/api/ui/setup/wifi/scan');
      const data = await res.json();
      setWifiNetworks(data.networks || []);
    } catch { /* ignore */ }
    setScanning(false);
  };

  const connectWifi = async () => {
    if (!selectedSsid) return;
    setConnecting(selectedSsid);
    setConnectError('');
    try {
      const res = await fetch('/api/ui/setup/wifi/connect', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ssid: selectedSsid, password }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setConnectError(body.detail || t('common.error'));
      } else {
        setPassword('');
        setSelectedSsid('');
        fetchStatus();
      }
    } catch (e: any) { setConnectError(e.message); }
    setConnecting(null);
  };

  return (
    <div className="space-y-8">
      <div>
        <h3 className="text-xl font-semibold mb-1">{t('settings.networkTitle')}</h3>
        <p className="text-sm text-zinc-400">{t('settings.networkDesc')}</p>
      </div>

      {/* Current Status */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
        <h4 className="font-medium mb-4">{t('settings.networkStatus')}</h4>
        {netStatus ? (
          <div className="space-y-3">
            <div className="flex items-center justify-between text-sm">
              <span className="text-zinc-400">{t('settings.internet')}</span>
              <span className={netStatus.internet ? "text-emerald-500" : "text-red-400"}>
                {netStatus.internet ? t('settings.connected') : t('settings.disconnected')}
              </span>
            </div>
            <div className="flex items-center justify-between text-sm">
              <span className="text-zinc-400">IP</span>
              <span className="text-zinc-200 font-mono text-xs">{netStatus.ip}</span>
            </div>
            {netStatus.interfaces?.map((iface: any) => (
              <div key={iface.name} className="flex items-center justify-between text-sm">
                <span className="text-zinc-400">{iface.name} ({iface.type})</span>
                <span className="text-zinc-200 font-mono text-xs">{iface.ip}</span>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-sm text-zinc-500">{t('common.loading')}</div>
        )}
      </div>

      {/* WiFi Networks */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
        <div className="flex items-center justify-between mb-4">
          <h4 className="font-medium">{t('settings.wifiNetworks')}</h4>
          <button onClick={scanWifi} disabled={scanning}
            className="text-xs text-zinc-400 hover:text-zinc-200 flex items-center gap-1.5 transition-colors">
            <RefreshCw size={14} className={scanning ? 'animate-spin' : ''} />
            {t('settings.scan')}
          </button>
        </div>
        {!netStatus?.nmcli_available && (
          <div className="text-xs text-amber-400 bg-amber-500/10 rounded-lg px-3 py-2 mb-3">
            {t('settings.nmcliNotAvailable')}
          </div>
        )}
        <div className="space-y-2 max-h-[240px] overflow-y-auto">
          {wifiNetworks.map(net => (
            <button key={net.ssid} onClick={() => { setSelectedSsid(net.ssid); setPassword(''); setConnectError(''); }}
              className={cn("w-full p-3 rounded-lg border flex items-center justify-between text-sm transition-all",
                selectedSsid === net.ssid ? "border-emerald-500 bg-emerald-500/10" : "border-zinc-800 bg-zinc-950 hover:border-zinc-700")}>
              <div className="flex items-center gap-2">
                <Wifi size={16} className={net.connected ? "text-emerald-500" : "text-zinc-400"} />
                <span>{net.ssid}</span>
                {net.security && <Lock size={12} className="text-zinc-500" />}
              </div>
              <span className="text-xs text-zinc-500">{net.signal}%</span>
            </button>
          ))}
          {wifiNetworks.length === 0 && !scanning && (
            <div className="text-center text-sm text-zinc-500 py-4">{t('settings.clickScan')}</div>
          )}
        </div>
        {selectedSsid && (
          <div className="mt-4 space-y-3">
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
              placeholder={t('settings.wifiPassword')}
              className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-emerald-500" />
            {connectError && <div className="text-xs text-red-400">{connectError}</div>}
            <button onClick={connectWifi} disabled={connecting !== null}
              className="px-4 py-2 rounded-lg bg-emerald-500 text-zinc-950 text-sm font-medium hover:bg-emerald-400 disabled:opacity-50">
              {connecting ? t('settings.connecting') : t('settings.connect')}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// ================================================================ //
//  System Settings                                                    //
// ================================================================ //

function SystemSettings() {
  const { t } = useTranslation();
  const [autoStopRam, setAutoStopRam] = useState(true);
  const [stopLlmTemp, setStopLlmTemp] = useState(true);
  const [resetting, setResetting] = useState(false);

  const saveSetting = async (key: string, value: boolean) => {
    try {
      await fetch('/api/ui/setup/config/update', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ section: 'system', key, value }),
      });
    } catch { /* ignore */ }
  };

  const resetWizard = async () => {
    if (!confirm(t('settings.resetWizardConfirm'))) return;
    setResetting(true);
    try {
      const res = await fetch('/api/ui/wizard/reset', { method: 'POST' });
      if (res.ok) {
        window.location.href = '/';
      }
    } catch { /* ignore */ } finally {
      setResetting(false);
    }
  };

  return (
    <div className="space-y-8">
      <div>
        <h3 className="text-xl font-semibold mb-1">{t('settings.systemTitle')}</h3>
        <p className="text-sm text-zinc-400">{t('settings.systemDesc')}</p>
      </div>
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
        <h4 className="font-medium mb-4">{t('settings.degradationStrategy')}</h4>
        <div className="space-y-4">
          <label className="flex items-center gap-3">
            <input type="checkbox" checked={autoStopRam}
              onChange={(e) => { setAutoStopRam(e.target.checked); saveSetting('auto_stop_low_ram', e.target.checked); }}
              className="rounded border-zinc-700 bg-zinc-950 text-emerald-500 focus:ring-emerald-500 focus:ring-offset-zinc-900" />
            <span className="text-sm text-zinc-300">{t('settings.autoStopAutomation')}</span>
          </label>
          <label className="flex items-center gap-3">
            <input type="checkbox" checked={stopLlmTemp}
              onChange={(e) => { setStopLlmTemp(e.target.checked); saveSetting('stop_llm_high_temp', e.target.checked); }}
              className="rounded border-zinc-700 bg-zinc-950 text-emerald-500 focus:ring-emerald-500 focus:ring-offset-zinc-900" />
            <span className="text-sm text-zinc-300">{t('settings.stopLlmOnHighTemp')}</span>
          </label>
        </div>
      </div>
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
        <h4 className="font-medium mb-2">{t('settings.resetWizardTitle')}</h4>
        <p className="text-sm text-zinc-400 mb-4">{t('settings.resetWizardDesc')}</p>
        <button
          onClick={resetWizard}
          disabled={resetting}
          className="px-4 py-2 rounded-lg text-sm font-medium bg-amber-500/10 text-amber-400 border border-amber-500/20 hover:bg-amber-500/20 transition-colors disabled:opacity-50"
        >
          {resetting ? t('common.loading') : t('settings.resetWizardBtn')}
        </button>
      </div>
    </div>
  );
}

// ================================================================ //
//  System Modules Settings                                           //
// ================================================================ //
function SystemModulesSettings() {
  const { t } = useTranslation();
  const modules = useStore(s => s.modules);
  const fetchModules = useStore(s => s.fetchModules);
  const widgetLayout = useStore(s => s.widgetLayout);
  const pinModule = useStore(s => s.pinModule);
  const unpinModule = useStore(s => s.unpinModule);
  const [selectedMod, setSelectedMod] = useState<string | null>(null);

  useEffect(() => { fetchModules(); }, [fetchModules]);

  const systemMods = modules.filter(m => m.type === 'SYSTEM');
  const selected = systemMods.find(m => m.name === selectedMod);

  return (
    <div>
      <div style={{ marginBottom: 20 }}>
        <h3 className="text-xl font-semibold mb-1">{t('settings.systemModules')}</h3>
        <p className="text-sm text-zinc-400">{t('settings.systemModulesDesc')}</p>
      </div>

      <div style={{ display: 'flex', gap: 16, height: 520 }}>
        {/* Module list */}
        <div style={{ width: 220, flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 4, overflowY: 'auto' }}>
          {systemMods.length === 0 ? (
            <div style={{ fontSize: 12, color: 'var(--tx3)', padding: '8px 0' }}>
              {t('settings.noSystemModules')}
            </div>
          ) : systemMods.map(m => {
            const isPinned = widgetLayout.pinned.includes(m.name);
            const isSelected = m.name === selectedMod;
            const isRunning = m.status === 'RUNNING';
            const hasWidget = !!m.ui?.widget?.file;
            return (
              <div
                key={m.name}
                onClick={() => setSelectedMod(isSelected ? null : m.name)}
                style={{
                  padding: '8px 10px', borderRadius: 8,
                  background: isSelected ? 'var(--sf2)' : 'var(--sf)',
                  border: `1px solid ${isSelected ? 'var(--b2)' : 'var(--b)'}`,
                  cursor: 'pointer', display: 'flex', flexDirection: 'column', gap: 5,
                  transition: 'all .15s',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <div style={{
                    width: 5, height: 5, borderRadius: '50%', flexShrink: 0,
                    background: isRunning ? 'var(--gr)' : 'var(--tx3)',
                  }} />
                  <span style={{
                    fontSize: 12, fontWeight: 500, color: 'var(--tx)',
                    flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  }}>
                    {m.name}
                  </span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  <span style={{ fontSize: 10, color: 'var(--tx3)', fontFamily: 'var(--font-mono)' }}>
                    :{m.port}
                  </span>
                  {hasWidget && (
                    <button
                      onClick={e => {
                        e.stopPropagation();
                        isPinned ? unpinModule(m.name) : pinModule(m.name);
                      }}
                      style={{
                        marginLeft: 'auto', fontSize: 9, padding: '2px 7px', borderRadius: 4,
                        background: isPinned ? 'rgba(79,140,247,.15)' : 'var(--sf3)',
                        color: isPinned ? 'var(--ac)' : 'var(--tx3)',
                        border: `1px solid ${isPinned ? 'rgba(79,140,247,.3)' : 'var(--b)'}`,
                        cursor: 'pointer', fontWeight: 500,
                      }}
                    >
                      {isPinned ? t('settings.pinned') : t('settings.pin')}
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>

        {/* Settings iframe */}
        <div style={{
          flex: 1, background: 'var(--sf)', border: '1px solid var(--b)',
          borderRadius: 12, overflow: 'hidden',
        }}>
          {selected && selected.ui?.settings ? (
            <iframe
              key={selected.name}
              src={`/api/ui/modules/${selected.name}/settings`}
              style={{ width: '100%', height: '100%', border: 'none', display: 'block' }}
              title={`${selected.name} settings`}
              sandbox="allow-scripts allow-same-origin allow-forms"
            />
          ) : selected ? (
            <div style={{
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              height: '100%', color: 'var(--tx3)', fontSize: 12,
            }}>
              {t('settings.noSettingsAvailable')}
            </div>
          ) : (
            <div style={{
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              height: '100%', color: 'var(--tx3)', fontSize: 12,
            }}>
              {t('settings.selectModuleToConfig')}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
