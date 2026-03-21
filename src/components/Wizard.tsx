import { useState, useEffect, useRef, useCallback } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { useTranslation } from 'react-i18next';
import { useStore } from '../store/useStore';
import { Check, ChevronRight, Wifi, Globe, Mic, User, Cloud, Download, Activity, AlertCircle, RefreshCw, Signal, Lock, Play, WifiOff, Cable, Radio } from 'lucide-react';
import { cn } from '../lib/utils';
import VirtualKeyboard from './VirtualKeyboard';

const STEP_ICONS = [Globe, Wifi, HomeIcon, Globe, Mic, Mic, User, Cloud, Download];

// Map frontend step number to backend step name + data builder
const STEP_MAP: Record<number, { name: string; buildData: (f: FormData) => Record<string, string> }> = {
  1: { name: 'language', buildData: (f) => ({ language: f.lang }) },
  2: { name: 'wifi', buildData: (f) => ({ ssid: f.wifi, password: f.wifiPassword }) },
  3: { name: 'device_name', buildData: (f) => ({ name: f.name }) },
  4: { name: 'timezone', buildData: (f) => ({ timezone: f.timezone }) },
  5: { name: 'stt_model', buildData: (f) => ({ model: f.stt }) },
  6: { name: 'tts_voice', buildData: (f) => ({ voice: f.tts }) },
  7: { name: 'admin_user', buildData: (f) => ({ username: f.username, pin: f.pin }) },
  8: { name: 'platform', buildData: (f) => ({ device_hash: f.platformHash }) },
  9: { name: 'import', buildData: (f) => ({ source: f.importSource }) },
};

interface FormData {
  lang: string;
  wifi: string;
  wifiPassword: string;
  name: string;
  timezone: string;
  stt: string;
  tts: string;
  username: string;
  pin: string;
  platformHash: string;
  importSource: string;
}

interface WifiNetwork {
  ssid: string;
  signal: number;
  security: string;
  connected: boolean;
}

interface SttModel {
  id: string;
  name: string;
  ram_mb: number;
  size_mb: number;
  quality: string;
  installed: boolean;
  active: boolean;
  fits_ram: boolean;
}

interface TtsVoice {
  id: string;
  name: string;
  language: string;
  gender: string;
  size_mb: number;
  installed: boolean;
  active: boolean;
}

interface TimezoneData {
  timezones: string[];
  common: string[];
  current: string;
}

function HomeIcon(props: any) {
  return <svg {...props} xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /><polyline points="9 22 9 12 15 12 15 22" /></svg>;
}

function SignalBars({ signal }: { signal: number }) {
  const bars = signal > 75 ? 4 : signal > 50 ? 3 : signal > 25 ? 2 : 1;
  return (
    <div className="flex items-end gap-0.5 h-4">
      {[1, 2, 3, 4].map(b => (
        <div key={b} className={cn("w-1 rounded-sm", b <= bars ? "bg-emerald-500" : "bg-zinc-700")} style={{ height: `${b * 25}%` }} />
      ))}
    </div>
  );
}

export default function Wizard() {
  const { t } = useTranslation();
  const selectedLanguage = useStore((state) => state.selectedLanguage);
  const setSelectedLanguage = useStore((state) => state.setSelectedLanguage);
  const [step, setStep] = useState(1);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const setConfigured = useStore((state) => state.setConfigured);
  const setUser = useStore((state) => state.setUser);

  // Real API data
  const [wifiNetworks, setWifiNetworks] = useState<WifiNetwork[]>([]);
  const [wifiLoading, setWifiLoading] = useState(false);
  const [wifiAvailable, setWifiAvailable] = useState(true);
  const [wifiEnabled, setWifiEnabled] = useState(false);
  const [wifiToggling, setWifiToggling] = useState(false);
  const [wifiAdapterFound, setWifiAdapterFound] = useState(true);
  const [ethernetConnected, setEthernetConnected] = useState(false);
  const [ethernetIp, setEthernetIp] = useState<string | null>(null);
  const [hasInternet, setHasInternet] = useState(false);
  const [wifiConnecting, setWifiConnecting] = useState(false);
  const [wifiConnected, setWifiConnected] = useState(false);
  const [wifiConnectedSsid, setWifiConnectedSsid] = useState<string | null>(null);
  const [apActive, setApActive] = useState(false);
  const [apSsid, setApSsid] = useState('');
  const [apPassword, setApPassword] = useState('');
  const [apIp, setApIp] = useState('');
  const [apStarting, setApStarting] = useState(false);
  const [sttModels, setSttModels] = useState<SttModel[]>([]);
  const [sttRamInfo, setSttRamInfo] = useState({ total: 0, available: 0 });
  const [ttsVoices, setTtsVoices] = useState<TtsVoice[]>([]);
  const [tzData, setTzData] = useState<TimezoneData | null>(null);
  const [tzSearch, setTzSearch] = useState('');
  const [previewingVoice, setPreviewingVoice] = useState<string | null>(null);

  // Virtual keyboard state
  type KbTarget = 'wifiPassword' | 'name' | 'tzSearch' | 'username' | 'pin' | null;
  const [kbTarget, setKbTarget] = useState<KbTarget>(null);
  const kbInputRefs = useRef<Record<string, HTMLInputElement | null>>({});
  const isTouchDevice = typeof window !== 'undefined' && ('ontouchstart' in window || navigator.maxTouchPoints > 0);

  const openKb = useCallback((target: KbTarget) => {
    if (isTouchDevice) setKbTarget(target);
  }, [isTouchDevice]);

  const closeKb = useCallback(() => setKbTarget(null), []);

  const kbKeyPress = useCallback((key: string) => {
    if (!kbTarget) return;
    switch (kbTarget) {
      case 'wifiPassword':
        setFormData(prev => ({ ...prev, wifiPassword: prev.wifiPassword + key }));
        setWifiConnected(false);
        break;
      case 'name':
        setFormData(prev => ({ ...prev, name: prev.name + key }));
        break;
      case 'tzSearch':
        setTzSearch(prev => prev + key);
        break;
      case 'username':
        setFormData(prev => ({ ...prev, username: prev.username + key }));
        break;
      case 'pin':
        if (/\d/.test(key) && formData.pin.length < 8)
          setFormData(prev => ({ ...prev, pin: prev.pin + key }));
        break;
    }
  }, [kbTarget, formData.pin.length]);

  const kbBackspace = useCallback(() => {
    if (!kbTarget) return;
    switch (kbTarget) {
      case 'wifiPassword':
        setFormData(prev => ({ ...prev, wifiPassword: prev.wifiPassword.slice(0, -1) }));
        setWifiConnected(false);
        break;
      case 'name':
        setFormData(prev => ({ ...prev, name: prev.name.slice(0, -1) }));
        break;
      case 'tzSearch':
        setTzSearch(prev => prev.slice(0, -1));
        break;
      case 'username':
        setFormData(prev => ({ ...prev, username: prev.username.slice(0, -1) }));
        break;
      case 'pin':
        setFormData(prev => ({ ...prev, pin: prev.pin.slice(0, -1) }));
        break;
    }
  }, [kbTarget]);

  const kbEnter = useCallback(() => {
    closeKb();
  }, [closeKb]);

  const [formData, setFormData] = useState<FormData>({
    lang: selectedLanguage,
    wifi: '',
    wifiPassword: '',
    name: t('wizard.defaultHomeName'),
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'Europe/Kyiv',
    stt: 'base',
    tts: 'ru_RU-irina-medium',
    username: 'admin',
    pin: '',
    platformHash: '',
    importSource: '',
  });

  // Fetch WiFi networks when entering step 2
  useEffect(() => {
    closeKb(); // close keyboard on step change
    if (step === 2) {
      fetchNetworkStatus();
    }
    if (step === 4 && !tzData) fetchTimezones();
    if (step === 5 && sttModels.length === 0) fetchSttModels();
    if (step === 6 && ttsVoices.length === 0) fetchTtsVoices();
  }, [step]);

  const fetchNetworkStatus = async () => {
    try {
      const res = await fetch('/api/ui/setup/network/status');
      const data = await res.json();
      setEthernetConnected(data.ethernet?.connected ?? false);
      setEthernetIp(data.ethernet?.ip ?? null);
      setHasInternet(data.internet ?? false);
      setWifiEnabled(data.wifi?.enabled ?? false);
      setWifiAdapterFound(data.wifi?.adapter_found ?? false);

      // Check AP status
      try {
        const apRes = await fetch('/api/ui/setup/ap/status');
        const apData = await apRes.json();
        if (apData.active) {
          setApActive(true);
          setApSsid(apData.ssid);
          setApPassword(apData.password);
          setApIp(apData.ip);
          return; // AP is active, skip WiFi scan
        }
      } catch { /* ap status unavailable */ }

      if (data.wifi?.enabled) {
        fetchWifiNetworks();
      }

      // Auto-start AP when no connectivity at all
      if (!data.ethernet?.connected && !data.wifi?.connected && data.wifi?.adapter_found) {
        startAp();
      }
    } catch {
      // fallback — try wifi scan directly
      fetchWifiNetworks();
    }
  };

  const fetchWifiNetworks = async () => {
    setWifiLoading(true);
    try {
      const res = await fetch('/api/ui/setup/wifi/scan');
      const data = await res.json();
      setWifiNetworks(data.networks || []);
      setWifiAvailable(data.available !== false);
    } catch {
      setWifiNetworks([]);
    } finally {
      setWifiLoading(false);
    }
  };

  const toggleWifi = async (enable: boolean) => {
    setWifiToggling(true);
    try {
      // Stop AP first if active — AP and client mode are mutually exclusive
      if (enable && apActive) {
        await fetch('/api/ui/setup/ap/stop', { method: 'POST' });
        setApActive(false);
      }
      const res = await fetch('/api/ui/setup/wifi/toggle', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enable }),
      });
      if (res.ok) {
        setWifiEnabled(enable);
        if (enable) {
          // Wait a moment for the adapter to come up, then scan
          await new Promise(r => setTimeout(r, 2000));
          await fetchWifiNetworks();
        } else {
          setWifiNetworks([]);
          setFormData(prev => ({ ...prev, wifi: '', wifiPassword: '' }));
        }
      }
    } catch {
      // toggle failed silently
    } finally {
      setWifiToggling(false);
    }
  };

  const fetchTimezones = async () => {
    try {
      const res = await fetch('/api/ui/setup/timezones');
      const data: TimezoneData = await res.json();
      setTzData(data);
      if (data.current && !formData.timezone) {
        setFormData(prev => ({ ...prev, timezone: data.current }));
      }
    } catch { /* fallback to hardcoded list */ }
  };

  const fetchSttModels = async () => {
    try {
      const res = await fetch('/api/ui/setup/stt/models');
      const data = await res.json();
      setSttModels(data.models || []);
      setSttRamInfo({ total: data.ram_total_mb || 0, available: data.ram_available_mb || 0 });
      if (data.active) setFormData(prev => ({ ...prev, stt: data.active }));
    } catch { /* use defaults */ }
  };

  const fetchTtsVoices = async () => {
    try {
      const res = await fetch('/api/ui/setup/tts/voices');
      const data = await res.json();
      setTtsVoices(data.voices || []);
      if (data.active) setFormData(prev => ({ ...prev, tts: data.active }));
    } catch { /* use defaults */ }
  };

  const connectWifi = async () => {
    if (!formData.wifi) return;
    setWifiConnecting(true);
    setError(null);
    const wasApActive = apActive;
    try {
      const res = await fetch('/api/ui/setup/wifi/connect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ssid: formData.wifi, password: formData.wifiPassword }),
      });
      const data = await res.json();
      if (res.ok && data.status === 'connected') {
        setWifiConnected(true);
        setWifiConnectedSsid(formData.wifi);
        setApActive(false);
      } else {
        setError(data.detail || t('wizard.wifiConnectFailed'));
        setWifiConnected(false);
        if (wasApActive) await startAp();
      }
    } catch {
      setError(t('wizard.wifiConnectFailed'));
      setWifiConnected(false);
      if (wasApActive) await startAp();
    } finally {
      setWifiConnecting(false);
    }
  };

  const startAp = async () => {
    setApStarting(true);
    try {
      const res = await fetch('/api/ui/setup/ap/start', { method: 'POST' });
      const data = await res.json();
      if (res.ok && data.status === 'active') {
        setApActive(true);
        setApSsid(data.ssid);
        setApPassword(data.password);
        setApIp(data.ip);
        setWifiEnabled(false);
      }
    } catch { /* AP start failed silently */ }
    finally { setApStarting(false); }
  };

  const stopAp = async () => {
    try {
      await fetch('/api/ui/setup/ap/stop', { method: 'POST' });
      setApActive(false);
      setApSsid('');
      setApPassword('');
      setApIp('');
    } catch { /* AP stop failed silently */ }
  };

  const previewVoice = async (voiceId: string) => {
    if (previewingVoice) return;
    setPreviewingVoice(voiceId);
    try {
      const sampleText = formData.lang === 'uk' ? 'Привіт, я ваш голосовий асистент.' : 'Hello, I am your voice assistant.';
      const res = await fetch('/api/ui/setup/tts/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: sampleText, voice: voiceId }),
      });
      if (res.ok) {
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const audio = new Audio(url);
        audio.onended = () => { URL.revokeObjectURL(url); setPreviewingVoice(null); };
        audio.onerror = () => { URL.revokeObjectURL(url); setPreviewingVoice(null); };
        await audio.play();
      } else {
        setPreviewingVoice(null);
      }
    } catch {
      setPreviewingVoice(null);
    }
  };



  const nextStep = async () => {
    const mapping = STEP_MAP[step];
    if (!mapping) return;

    setError(null);
    setSubmitting(true);
    try {
      const resp = await fetch('/api/ui/wizard/step', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ step: mapping.name, data: mapping.buildData(formData) }),
      });
      if (!resp.ok && resp.status !== 409) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body?.detail ?? `${t('common.error')} ${resp.status}`);
      }
      if (step === 9) {
        setUser({ name: formData.username, role: 'admin' });
        setConfigured(true);
      } else {
        setStep(s => s + 1);
      }
    } catch (e: any) {
      setError(e.message ?? t('wizard.unknownError'));
    } finally {
      setSubmitting(false);
    }
  };

  const skipStep = async () => {
    // Steps 2, 8 and 9 can be skipped
    const mapping = STEP_MAP[step];
    if (!mapping) return;
    setError(null);
    setSubmitting(true);
    try {
      await fetch('/api/ui/wizard/step', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ step: mapping.name, data: {} }),
      });
      if (step === 9) {
        setUser({ name: formData.username || 'Admin', role: 'admin' });
        setConfigured(true);
      } else {
        setStep(s => s + 1);
      }
    } catch {
      // skip anyway
      if (step === 9) {
        setUser({ name: formData.username || 'Admin', role: 'admin' });
        setConfigured(true);
      } else {
        setStep(s => s + 1);
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className={cn("min-h-screen bg-zinc-950 text-zinc-50 flex flex-col items-center p-3 pt-4 font-sans transition-[padding]", kbTarget && "pb-52")}>
      <div className="w-full max-w-3xl">
        {/* Header — compact for 7" */}
        <div className="flex items-center justify-center gap-3 mb-3">
          <div className="inline-flex items-center justify-center w-9 h-9 rounded-xl bg-emerald-500/10 text-emerald-500 shrink-0">
            <Activity size={18} />
          </div>
          <div>
            <h1 className="text-lg font-semibold tracking-tight leading-tight">{t('wizard.coreTitle')}</h1>
            <p className="text-zinc-500 text-xs">{t('wizard.initialSetup')}</p>
          </div>
        </div>

        {/* Progress Bar — compact */}
        <div className="flex items-center justify-between mb-3 relative px-1">
          <div className="absolute left-0 top-1/2 -translate-y-1/2 w-full h-0.5 bg-zinc-800 -z-10 rounded-full overflow-hidden">
            <motion.div
              className="h-full bg-emerald-500"
              initial={{ width: 0 }}
              animate={{ width: `${((step - 1) / (STEP_ICONS.length - 1)) * 100}%` }}
              transition={{ duration: 0.3 }}
            />
          </div>
          {STEP_ICONS.map((_Icon, idx) => {
            const sid = idx + 1;
            const isActive = sid === step;
            const isPast = sid < step;
            return (
              <div key={sid} className="flex flex-col items-center">
                <div className={cn(
                  "w-7 h-7 rounded-full flex items-center justify-center text-xs font-medium transition-colors border-2",
                  isActive ? "bg-zinc-900 border-emerald-500 text-emerald-500" :
                    isPast ? "bg-emerald-500 border-emerald-500 text-zinc-950" :
                      "bg-zinc-900 border-zinc-800 text-zinc-500"
                )}>
                  {isPast ? <Check size={12} /> : sid}
                </div>
              </div>
            );
          })}
        </div>

        {/* Content Area — compact for 7" */}
        <div className="bg-zinc-900/50 border border-zinc-800 rounded-xl p-4 backdrop-blur-sm flex flex-col flex-1">
          <AnimatePresence mode="wait">
            <motion.div
              key={step}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              transition={{ duration: 0.2 }}
              className="flex-1"
            >
              {step === 1 && (
                <div className="space-y-3">
                  <h2 className="text-base font-medium">{t('wizard.selectLanguage')}</h2>
                  <p className="text-zinc-400 text-xs">{t('wizard.languageDesc')}</p>
                  <div className="space-y-2">
                    {[
                      { id: 'uk', name: 'Українська' },
                      { id: 'en', name: 'English' },
                    ].map(lang => (
                      <button
                        key={lang.id}
                        onClick={() => { setFormData({ ...formData, lang: lang.id }); setSelectedLanguage(lang.id); }}
                        className={cn(
                          "w-full p-3 rounded-lg border flex items-center justify-between transition-all",
                          formData.lang === lang.id
                            ? "border-emerald-500 bg-emerald-500/10"
                            : "border-zinc-800 bg-zinc-900 hover:border-zinc-700"
                        )}
                      >
                        <span className="font-medium text-sm">{lang.name}</span>
                        {formData.lang === lang.id && <Check size={16} className="text-emerald-500" />}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {step === 2 && (
                <div className="space-y-3">
                  <h2 className="text-base font-medium">{t('wizard.wifiTitle')}</h2>
                  <p className="text-zinc-400 text-xs">{t('wizard.wifiDesc')}</p>

                  {/* AP Mode panel — when active */}
                  {apActive && (
                    <div className="rounded-lg border border-cyan-500/30 bg-cyan-500/5 px-3 py-2.5 space-y-2">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <Radio size={16} className="text-cyan-400" />
                          <span className="font-medium text-xs text-cyan-400">{t('wizard.apActive')}</span>
                        </div>
                        <button
                          onClick={stopAp}
                          className="text-[10px] text-zinc-400 hover:text-zinc-200 transition-colors"
                        >
                          {t('wizard.apStopBtn')}
                        </button>
                      </div>
                      <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
                        <div><span className="text-zinc-500">{t('wizard.apNetwork')}:</span> <span className="text-zinc-200 font-mono">{apSsid}</span></div>
                        <div><span className="text-zinc-500">{t('wizard.apPassword')}:</span> <span className="text-zinc-200 font-mono">{apPassword}</span></div>
                      </div>
                      <div className="text-[10px] text-zinc-400">
                        {t('wizard.apOpenUrl')}: <span className="text-cyan-400 font-mono">http://{apIp}:8080</span>
                      </div>
                    </div>
                  )}

                  {/* WiFi toggle — hidden when AP is active */}
                  {!apActive && (
                    <div className="flex items-center justify-between px-3 py-2.5 rounded-lg border border-zinc-800 bg-zinc-900">
                      <div className="flex items-center gap-2">
                        {wifiEnabled ? <Wifi size={16} className="text-emerald-500" /> : <WifiOff size={16} className="text-zinc-500" />}
                        <div>
                          <span className="font-medium text-xs">{t('wizard.wifiAdapter')}</span>
                          <p className="text-[10px] text-zinc-500">
                            {!wifiAdapterFound ? t('wizard.wifiAdapterNotFound') : wifiEnabled ? t('wizard.wifiAdapterOn') : t('wizard.wifiAdapterOff')}
                          </p>
                        </div>
                      </div>
                      <button
                        onClick={() => toggleWifi(!wifiEnabled)}
                        disabled={wifiToggling || !wifiAdapterFound}
                        className={cn(
                          "relative inline-flex h-7 w-12 shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500 focus-visible:ring-offset-2 focus-visible:ring-offset-zinc-900",
                          wifiEnabled ? "bg-emerald-500" : "bg-zinc-600",
                          (wifiToggling || !wifiAdapterFound) && "opacity-50 cursor-not-allowed"
                        )}
                        role="switch"
                        aria-checked={wifiEnabled}
                      >
                        <span className={cn(
                          "pointer-events-none inline-block h-5 w-5 rounded-full bg-white shadow-lg ring-0 transition-transform duration-200 ease-in-out",
                          wifiEnabled ? "translate-x-[22px]" : "translate-x-[3px]"
                        )} />
                        {wifiToggling && (
                          <span className="absolute inset-0 flex items-center justify-center">
                            <span className="h-3.5 w-3.5 rounded-full border-2 border-white border-t-transparent animate-spin" />
                          </span>
                        )}
                      </button>
                    </div>
                  )}

                  {/* AP start button — when no connectivity and not yet active */}
                  {!apActive && wifiAdapterFound && !wifiEnabled && !ethernetConnected && (
                    <button
                      onClick={startAp}
                      disabled={apStarting}
                      className="w-full flex items-center gap-2 px-3 py-2.5 rounded-lg border border-dashed border-cyan-500/30 bg-cyan-500/5 hover:border-cyan-500/50 transition-colors disabled:opacity-50"
                    >
                      <Radio size={16} className="text-cyan-400 shrink-0" />
                      <div className="text-left flex-1">
                        <span className="font-medium text-xs text-cyan-400">{t('wizard.apTitle')}</span>
                        <p className="text-[10px] text-zinc-500">{t('wizard.apDesc')}</p>
                      </div>
                      {apStarting && <span className="w-3 h-3 border-2 border-cyan-400 border-t-transparent rounded-full animate-spin shrink-0" />}
                    </button>
                  )}

                  {/* Ethernet status banner — hidden when WiFi adapter is active or AP active */}
                  {!wifiEnabled && !apActive && ethernetConnected && (
                    <div className="flex items-center gap-2 text-xs bg-emerald-500/10 border border-emerald-500/20 rounded-lg px-3 py-2">
                      <Cable size={14} className="text-emerald-500 shrink-0" />
                      <div>
                        <span className="text-emerald-400 font-medium">{t('wizard.ethernetConnected')}</span>
                        {ethernetIp && <span className="text-zinc-400 ml-2">IP: {ethernetIp}</span>}
                      </div>
                    </div>
                  )}

                  {!wifiAdapterFound && !ethernetConnected && !apActive && (
                    <div className="text-xs text-amber-400 bg-amber-500/10 border border-amber-500/20 rounded-lg px-3 py-2">
                      {t('wizard.wifiNotAvailable')}
                    </div>
                  )}

                  {/* WiFi networks list — only when enabled and AP not active */}
                  {wifiEnabled && !apActive && (
                    <>
                      <div className="flex justify-end">
                        <button onClick={fetchWifiNetworks} disabled={wifiLoading}
                          className="text-[10px] text-zinc-400 hover:text-zinc-200 flex items-center gap-1 transition-colors">
                          <RefreshCw size={12} className={wifiLoading ? 'animate-spin' : ''} />
                          {t('common.refresh')}
                        </button>
                      </div>
                      {wifiLoading ? (
                        <div className="flex items-center justify-center py-6">
                          <div className="w-5 h-5 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin" />
                          <span className="ml-2 text-xs text-zinc-400">{t('wizard.wifiScanning')}</span>
                        </div>
                      ) : (
                        <div className="space-y-1.5 max-h-[150px] overflow-y-auto pr-1">
                          {wifiNetworks.length > 0 ? wifiNetworks.map(net => (
                            <button
                              key={net.ssid}
                              onClick={() => { setFormData({ ...formData, wifi: net.ssid }); setWifiConnected(false); setWifiConnectedSsid(null); }}
                              className={cn(
                                "w-full px-3 py-2 rounded-lg border flex items-center justify-between transition-all",
                                formData.wifi === net.ssid
                                  ? "border-emerald-500 bg-emerald-500/10"
                                  : "border-zinc-800 bg-zinc-900 hover:border-zinc-700"
                              )}
                            >
                              <div className="flex items-center gap-2">
                                <Wifi size={14} className={formData.wifi === net.ssid ? "text-emerald-500" : "text-zinc-400"} />
                                <span className="text-sm font-medium">{net.ssid}</span>
                                {net.security && <Lock size={10} className="text-zinc-500" />}
                                {net.connected && <span className="text-[10px] text-emerald-500 bg-emerald-500/10 px-1 py-0.5 rounded">{t('wizard.wifiConnected')}</span>}
                              </div>
                              <div className="flex items-center gap-2">
                                <SignalBars signal={net.signal} />
                                {formData.wifi === net.ssid && <Check size={14} className="text-emerald-500" />}
                              </div>
                            </button>
                          )) : (
                            <div className="text-center py-4 text-zinc-500 text-xs">{t('wizard.wifiNoNetworks')}</div>
                          )}
                        </div>
                      )}
                      {formData.wifi && (
                        <div className="space-y-2">
                          <div>
                            <label className="block text-xs font-medium text-zinc-400 mb-1">{t('wizard.wifiPassword')}</label>
                            <input
                              ref={el => { kbInputRefs.current['wifiPassword'] = el; }}
                              type="password"
                              value={formData.wifiPassword}
                              onChange={(e) => { setFormData({ ...formData, wifiPassword: e.target.value }); setWifiConnected(false); }}
                              onFocus={() => openKb('wifiPassword')}
                              readOnly={isTouchDevice}
                              className={cn("w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-zinc-50 focus:outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 transition-all", kbTarget === 'wifiPassword' && 'border-emerald-500 ring-1 ring-emerald-500')}
                              placeholder={t('wizard.wifiPasswordPlaceholder')}
                            />
                          </div>
                          {wifiConnected && wifiConnectedSsid === formData.wifi ? (
                            <div className="flex items-center gap-2 text-xs text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 rounded-lg px-3 py-2">
                              <Check size={14} />
                              <span>{t('wizard.wifiVerified')}</span>
                            </div>
                          ) : (
                            <button
                              onClick={connectWifi}
                              disabled={wifiConnecting || !formData.wifiPassword}
                              className="w-full py-2 rounded-lg text-xs font-medium bg-emerald-500 text-zinc-950 hover:bg-emerald-400 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
                            >
                              {wifiConnecting ? (
                                <><span className="w-3 h-3 border-2 border-zinc-950 border-t-transparent rounded-full animate-spin" /> {t('wizard.wifiConnecting')}</>
                              ) : (
                                t('wizard.wifiConnectBtn')
                              )}
                            </button>
                          )}
                        </div>
                      )}
                    </>
                  )}
                </div>
              )}

              {step === 3 && (
                <div className="space-y-3">
                  <h2 className="text-base font-medium">{t('wizard.deviceNameTitle')}</h2>
                  <p className="text-zinc-400 text-xs">{t('wizard.deviceNameDesc')}</p>
                  <input
                    ref={el => { kbInputRefs.current['name'] = el; }}
                    type="text"
                    value={formData.name}
                    onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                    onFocus={() => openKb('name')}
                    readOnly={isTouchDevice}
                    className={cn("w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-zinc-50 focus:outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 transition-all", kbTarget === 'name' && 'border-emerald-500 ring-1 ring-emerald-500')}
                    placeholder={t('wizard.deviceNamePlaceholder')}
                  />
                </div>
              )}

              {step === 4 && (
                <div className="space-y-3">
                  <h2 className="text-base font-medium">{t('wizard.timezoneTitle')}</h2>
                  <p className="text-zinc-400 text-xs">{t('wizard.timezoneDesc')}</p>
                  <input
                    ref={el => { kbInputRefs.current['tzSearch'] = el; }}
                    type="text"
                    value={tzSearch}
                    onChange={(e) => setTzSearch(e.target.value)}
                    onFocus={() => openKb('tzSearch')}
                    readOnly={isTouchDevice}
                    className={cn("w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-zinc-50 focus:outline-none focus:border-emerald-500 transition-all", kbTarget === 'tzSearch' && 'border-emerald-500 ring-1 ring-emerald-500')}
                    placeholder={t('wizard.timezoneSearch')}
                  />
                  <div className="space-y-1.5 max-h-[180px] overflow-y-auto pr-1">
                    {(tzData?.timezones || ['Europe/Kyiv', 'Europe/Moscow', 'Europe/London', 'America/New_York', 'UTC'])
                      .filter(tz => !tzSearch || tz.toLowerCase().includes(tzSearch.toLowerCase()))
                      .slice(0, 50)
                      .map(tz => (
                        <button
                          key={tz}
                          onClick={() => { setFormData({ ...formData, timezone: tz }); setTzSearch(''); }}
                          className={cn(
                            "w-full px-3 py-2 rounded-lg border flex items-center justify-between transition-all text-left",
                            formData.timezone === tz
                              ? "border-emerald-500 bg-emerald-500/10"
                              : "border-zinc-800 bg-zinc-900 hover:border-zinc-700"
                          )}
                        >
                          <span className="font-medium text-xs">{tz.replace(/_/g, ' ')}</span>
                          {formData.timezone === tz && <Check size={14} className="text-emerald-500" />}
                        </button>
                      ))}
                  </div>
                  {formData.timezone && (
                    <div className="text-xs text-zinc-400">
                      {t('wizard.timezoneSelected')}: <span className="text-zinc-200">{formData.timezone}</span>
                    </div>
                  )}
                </div>
              )}

              {step === 5 && (
                <div className="space-y-3">
                  <h2 className="text-base font-medium">{t('wizard.sttTitle')}</h2>
                  <p className="text-zinc-400 text-xs">{t('wizard.sttDesc')}</p>
                  {sttRamInfo.available > 0 && (
                    <div className="text-xs text-zinc-500">
                      RAM: {sttRamInfo.available} MB {t('wizard.sttAvailable')} / {sttRamInfo.total} MB {t('wizard.sttTotal')}
                    </div>
                  )}
                  <div className="space-y-2">
                    {(sttModels.length > 0 ? sttModels : [
                      { id: 'tiny', name: 'Tiny', ram_mb: 150, size_mb: 75, quality: 'basic', installed: false, active: false, fits_ram: true },
                      { id: 'base', name: 'Base', ram_mb: 250, size_mb: 142, quality: 'good', installed: false, active: false, fits_ram: true },
                      { id: 'small', name: 'Small', ram_mb: 500, size_mb: 466, quality: 'high', installed: false, active: false, fits_ram: true },
                    ]).map(m => (
                      <button
                        key={m.id}
                        onClick={() => setFormData({ ...formData, stt: m.id })}
                        disabled={!m.fits_ram}
                        className={cn(
                          "w-full px-3 py-2.5 rounded-lg border flex items-center justify-between text-left transition-all",
                          !m.fits_ram ? "border-zinc-800 bg-zinc-900/50 opacity-50 cursor-not-allowed" :
                            formData.stt === m.id
                              ? "border-emerald-500 bg-emerald-500/10"
                              : "border-zinc-800 bg-zinc-900 hover:border-zinc-700"
                        )}
                      >
                        <div>
                          <div className="text-sm font-medium flex items-center gap-1.5 flex-wrap">
                            {m.name}
                            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-zinc-800 text-zinc-400">~{m.ram_mb}MB</span>
                            {m.installed && <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-500">{t('wizard.sttInstalled')}</span>}
                            {!m.fits_ram && <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-red-500/10 text-red-400">{t('wizard.sttNoRam')}</span>}
                          </div>
                          <div className="text-xs text-zinc-400 mt-0.5">
                            {m.id === 'tiny' ? t('wizard.sttTinyDesc') : m.id === 'base' ? t('wizard.sttBaseDesc') : m.id === 'small' ? t('wizard.sttSmallDesc') : m.quality}
                          </div>
                        </div>
                        {formData.stt === m.id && <Check size={16} className="text-emerald-500" />}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {step === 6 && (
                <div className="space-y-3">
                  <h2 className="text-base font-medium">{t('wizard.ttsTitle')}</h2>
                  <p className="text-zinc-400 text-xs">{t('wizard.ttsDesc')}</p>
                  <div className="grid grid-cols-2 gap-2">
                    {(ttsVoices.length > 0 ? ttsVoices : [
                      { id: 'ru_RU-irina-medium', name: 'Irina', language: 'ru', gender: 'female', size_mb: 50, installed: false, active: false },
                      { id: 'ru_RU-ruslan-medium', name: 'Ruslan', language: 'ru', gender: 'male', size_mb: 50, installed: false, active: false },
                      { id: 'en_US-amy-medium', name: 'Amy', language: 'en', gender: 'female', size_mb: 50, installed: false, active: false },
                      { id: 'en_US-ryan-high', name: 'Ryan', language: 'en', gender: 'male', size_mb: 60, installed: false, active: false },
                    ]).map(v => (
                      <div
                        key={v.id}
                        className={cn(
                          "px-3 py-2.5 rounded-lg border transition-all",
                          formData.tts === v.id
                            ? "border-emerald-500 bg-emerald-500/10"
                            : "border-zinc-800 bg-zinc-900 hover:border-zinc-700"
                        )}
                      >
                        <button
                          onClick={() => setFormData({ ...formData, tts: v.id })}
                          className="w-full text-left"
                        >
                          <div className="flex items-center justify-between mb-0.5">
                            <span className={cn("text-sm font-medium", formData.tts === v.id && "text-emerald-500")}>
                              {v.name}
                            </span>
                            {v.installed && <span className="text-[10px] px-1 py-0.5 rounded bg-emerald-500/10 text-emerald-500">{t('wizard.sttInstalled')}</span>}
                          </div>
                          <div className="text-[10px] text-zinc-500">
                            {v.language.toUpperCase()} · {v.gender === 'female' ? t('wizard.ttsFemale') : t('wizard.ttsMale')} · {v.size_mb}MB
                          </div>
                        </button>
                        <button
                          onClick={(e) => { e.stopPropagation(); previewVoice(v.id); }}
                          disabled={previewingVoice !== null}
                          className="mt-1.5 flex items-center gap-1 text-[10px] text-zinc-400 hover:text-emerald-400 transition-colors disabled:opacity-50"
                        >
                          <Play size={12} className={previewingVoice === v.id ? 'animate-pulse text-emerald-500' : ''} />
                          {previewingVoice === v.id ? t('wizard.ttsPlaying') : t('wizard.ttsPreview')}
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {step === 7 && (
                <div className="space-y-3">
                  <h2 className="text-base font-medium">{t('wizard.userTitle')}</h2>
                  <p className="text-zinc-400 text-xs">{t('wizard.userDesc')}</p>
                  <div className="space-y-3">
                    <div>
                      <label className="block text-xs font-medium text-zinc-400 mb-1">{t('wizard.userName')}</label>
                      <input
                        ref={el => { kbInputRefs.current['username'] = el; }}
                        type="text"
                        value={formData.username}
                        onChange={(e) => setFormData({ ...formData, username: e.target.value })}
                        onFocus={() => openKb('username')}
                        readOnly={isTouchDevice}
                        className={cn("w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-zinc-50 focus:outline-none focus:border-emerald-500 transition-all", kbTarget === 'username' && 'border-emerald-500 ring-1 ring-emerald-500')}
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-zinc-400 mb-1">{t('wizard.userPin')}</label>
                      <input
                        ref={el => { kbInputRefs.current['pin'] = el; }}
                        type="password"
                        maxLength={8}
                        value={formData.pin}
                        onChange={(e) => setFormData({ ...formData, pin: e.target.value.replace(/\D/g, '') })}
                        onFocus={() => openKb('pin')}
                        readOnly={isTouchDevice}
                        className={cn("w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-zinc-50 focus:outline-none focus:border-emerald-500 transition-all tracking-widest font-mono", kbTarget === 'pin' && 'border-emerald-500 ring-1 ring-emerald-500')}
                        placeholder={t('wizard.userPinPlaceholder')}
                      />
                    </div>
                  </div>
                </div>
              )}

              {step === 8 && (
                <div className="space-y-3">
                  <h2 className="text-base font-medium">{t('wizard.platformTitle')}</h2>
                  <p className="text-zinc-400 text-xs">{t('wizard.platformDesc')}</p>
                  <div className="flex flex-col items-center justify-center p-4 border border-zinc-800 border-dashed rounded-lg bg-zinc-900/50">
                    <div className="w-32 h-32 bg-white rounded-lg p-1.5 mb-3 flex items-center justify-center">
                      {/* Mock QR Code */}
                      <div className="w-full h-full bg-zinc-200 grid grid-cols-5 grid-rows-5 gap-1 p-1">
                        {Array.from({ length: 25 }).map((_, i) => (
                          <div key={i} className={Math.random() > 0.5 ? "bg-black" : "bg-transparent"} />
                        ))}
                      </div>
                    </div>
                    <p className="text-xs text-zinc-400 text-center">{t('wizard.platformQrHint')}</p>
                  </div>
                </div>
              )}

              {step === 9 && (
                <div className="space-y-3">
                  <h2 className="text-base font-medium">{t('wizard.importTitle')}</h2>
                  <p className="text-zinc-400 text-xs">{t('wizard.importDesc')}</p>
                  <div className="grid grid-cols-2 gap-2">
                    {[
                      { id: 'ha', name: t('wizard.importHa'), desc: t('wizard.importLocal') },
                      { id: 'tuya', name: t('wizard.importTuya'), desc: t('wizard.importCloud') },
                      { id: 'hue', name: t('wizard.importHue'), desc: t('wizard.importLocal') },
                      { id: 'mqtt', name: t('wizard.importMqtt'), desc: t('wizard.importLocal') },
                    ].map(sys => (
                      <button
                        key={sys.id}
                        className="px-3 py-2.5 rounded-lg border border-zinc-800 bg-zinc-900 hover:border-zinc-700 text-left transition-all group"
                      >
                        <div className="text-sm font-medium group-hover:text-emerald-400 transition-colors">{sys.name}</div>
                        <div className="text-[10px] text-zinc-500 mt-0.5">{sys.desc}</div>
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </motion.div>
          </AnimatePresence>

          {/* Footer Actions — compact */}
          <div className="mt-3 pt-3 border-t border-zinc-800 space-y-2">
            {error && (
              <div className="flex items-center gap-2 text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">
                <AlertCircle size={14} className="shrink-0" />
                <span>{error}</span>
              </div>
            )}
            <div className="flex items-center justify-between">
              <button
                onClick={() => {
                  if (step === 1) {
                    useStore.getState().setSetupStage('landing');
                  } else {
                    setStep(s => Math.max(1, s - 1));
                  }
                  setError(null);
                }}
                disabled={submitting}
                className="px-4 py-2 rounded-lg text-xs font-medium transition-colors text-zinc-400 hover:text-zinc-50 hover:bg-zinc-800 disabled:opacity-50"
              >
                {t('common.back')}
              </button>
              <div className="flex items-center gap-2">
                {(step === 8 || step === 9) && (
                  <button
                    onClick={skipStep}
                    disabled={submitting}
                    className="px-4 py-2 rounded-lg text-xs font-medium text-zinc-400 hover:text-zinc-50 hover:bg-zinc-800 transition-colors disabled:opacity-50"
                  >
                    {t('common.skip')}
                  </button>
                )}
                {step === 2 && !wifiAdapterFound && ethernetConnected && (
                  <button
                    onClick={skipStep}
                    disabled={submitting}
                    className="px-4 py-2 rounded-lg text-xs font-medium text-zinc-400 hover:text-zinc-50 hover:bg-zinc-800 transition-colors disabled:opacity-50"
                  >
                    {t('common.skip')}
                  </button>
                )}
                <button
                  onClick={nextStep}
                  disabled={
                    submitting ||
                    (step === 2 && !(wifiConnected && wifiConnectedSsid === formData.wifi) && !(ethernetConnected && !wifiAdapterFound)) ||
                    (step === 7 && (!formData.username || formData.pin.length < 4))
                  }
                  className="px-5 py-2 rounded-lg text-xs font-medium bg-emerald-500 text-zinc-950 hover:bg-emerald-400 transition-colors flex items-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed min-w-[80px] justify-center"
                >
                  {submitting ? (
                    <div className="w-4 h-4 border-2 border-zinc-950 border-t-transparent rounded-full animate-spin" />
                  ) : (
                    <>
                      {step === 9 ? t('common.finish') : t('common.next')}
                      {step !== 9 && <ChevronRight size={16} />}
                    </>
                  )}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
      {/* Virtual keyboard for touch input */}
      <VirtualKeyboard
        visible={kbTarget !== null}
        onKeyPress={kbKeyPress}
        onBackspace={kbBackspace}
        onEnter={kbEnter}
        onClose={closeKb}
        lang={formData.lang}
        numericOnly={kbTarget === 'pin'}
      />
    </div>
  );
}
