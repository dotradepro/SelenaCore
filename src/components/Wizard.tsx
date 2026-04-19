import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { useTranslation } from 'react-i18next';
import { useStore } from '../store/useStore';
import { Check, ChevronRight, Wifi, Globe, Mic, User, Cloud, Download, Activity, AlertCircle, RefreshCw, Signal, Lock, Play, WifiOff, Cable, Radio, Eye, EyeOff, Smartphone, QrCode } from 'lucide-react';
import { cn } from '../lib/utils';
import VirtualKeyboard from './VirtualKeyboard';
import ProvisionProgress from './ProvisionProgress';
import { LlmProviderStep } from './LlmProviderStep';
import LanguagePicker from './settings/LanguagePicker';

function HomeIcon(props: any) {
  return <svg {...props} xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /><polyline points="9 22 9 12 15 12 15 22" /></svg>;
}

const STEP_ICONS = [Globe, Wifi, HomeIcon, Globe, Mic, Mic, Cloud, User, Smartphone, Cloud, Download];

// Map frontend step number to backend step name + data builder
const STEP_MAP: Record<number, { name: string; buildData: (f: FormData) => Record<string, string | boolean> }> = {
  1: { name: 'language', buildData: (f) => ({ language: f.lang }) },
  2: { name: 'wifi', buildData: (f) => ({ ssid: f.wifi, password: f.wifiPassword }) },
  3: { name: 'device_name', buildData: (f) => ({ name: f.name }) },
  4: { name: 'timezone', buildData: (f) => ({ timezone: f.timezone }) },
  5: { name: 'stt_model', buildData: (f) => ({ model: f.stt }) },
  6: { name: 'tts_voice', buildData: (f) => ({ voice: f.tts, cuda: f.ttsDevice === 'gpu' }) },
  7: {
    name: 'llm_provider',
    buildData: (f) => {
      if (!f.llmProvider || f.llmProvider === 'skip') return { provider: 'skip' };
      const out: Record<string, string | boolean> = { provider: f.llmProvider };
      if (f.llmProvider === 'ollama') {
        out.url = f.llmUrl || 'http://localhost:11434';
        if (f.llmApiKey) out.api_key = f.llmApiKey;
      } else if (f.llmApiKey) {
        out.api_key = f.llmApiKey;
      }
      if (f.llmModel) out.model = f.llmModel;
      return out;
    },
  },
  8: { name: 'admin_user', buildData: (f) => ({ username: f.username, pin: f.pin }) },
  9: { name: 'home_devices', buildData: (f) => ({ device_name: f.kiosk_name }) },
  10: { name: 'platform', buildData: (f) => ({ device_hash: f.platformHash }) },
  11: { name: 'import', buildData: (f) => ({ source: f.importSource }) },
};

interface FormData {
  lang: string;
  wifi: string;
  wifiPassword: string;
  name: string;
  timezone: string;
  stt: string;
  tts: string;
  ttsDevice: 'gpu' | 'cpu';
  llmProvider: '' | 'skip' | 'ollama' | 'openai' | 'anthropic' | 'groq' | 'google';
  llmUrl: string;
  llmApiKey: string;
  llmModel: string;
  username: string;
  pin: string;
  kiosk_name: string;
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
  lang: string;
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
  // Persist wizard step across page reloads (kiosk watchdog in
  // useConnectionHealth.ts force-reloads the page when WS is stale for 60s;
  // without persistence the wizard restarts from step 1 mid-install and
  // re-asks language/wifi even though the backend already stored them).
  const WIZARD_STEP_KEY = 'selena-wizard-step';
  const [step, setStepRaw] = useState<number>(() => {
    try {
      const saved = parseInt(localStorage.getItem(WIZARD_STEP_KEY) || '', 10);
      return Number.isFinite(saved) && saved >= 1 ? saved : 1;
    } catch {
      return 1;
    }
  });
  const setStep = (value: number | ((prev: number) => number)) => {
    setStepRaw((prev) => {
      const next = typeof value === 'function' ? value(prev) : value;
      try { localStorage.setItem(WIZARD_STEP_KEY, String(next)); } catch {}
      return next;
    });
  };
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [gpuAvailable, setGpuAvailable] = useState(false);
  const [gpuType, setGpuType] = useState<string>('none');
  const setConfigured = useStore((state) => state.setConfigured);
  const fetchWizardStatus = useStore((state) => state.fetchWizardStatus);
  const fetchWizardRequirements = useStore((state) => state.fetchWizardRequirements);
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

  // Online STT (Vosk) catalog state. Raw catalog is fetched ONCE per step
  // entry; filtering/pagination happens client-side on sttRaw, derived into
  // sttCatalog via useMemo below. This removes the "refreshing on every
  // filter click" behaviour where picking a language triggered a fresh
  // network round-trip and hid the list mid-selection.
  const [sttRaw, setSttRaw] = useState<any[]>([]);
  const [sttLanguages, setSttLanguages] = useState<{ code: string; label: string; count: number }[]>([]);
  const [sttFilter, setSttFilter] = useState({ lang: '', quality: '', q: '', page: 1, per_page: 20 });
  const [sttLoading, setSttLoading] = useState(false);
  const [sttError, setSttError] = useState<string | null>(null);

  // Online TTS (Piper) catalog state — same single-fetch pattern.
  const [ttsRaw, setTtsRaw] = useState<any[]>([]);
  const [ttsLanguages, setTtsLanguages] = useState<{ code: string; label: string; count: number }[]>([]);
  const [ttsQualities, setTtsQualities] = useState<string[]>(['x_low', 'low', 'medium', 'high']);
  const [ttsFilter, setTtsFilter] = useState({ lang: '', quality: '', q: '', page: 1, per_page: 20 });
  const [ttsLoading, setTtsLoading] = useState(false);
  const [ttsError, setTtsError] = useState<string | null>(null);

  // Provisioning state
  const [provisioning, setProvisioning] = useState(false);
  const [provisionTasks, setProvisionTasks] = useState<{ id: string; label: string; status: string; error?: string }[]>([]);
  const [provisionCurrent, setProvisionCurrent] = useState('');
  const [provisionDone, setProvisionDone] = useState(false);
  const [provisionFailed, setProvisionFailed] = useState(false);
  const [provisionError, setProvisionError] = useState<string | null>(null);
  const [provisionCompleted, setProvisionCompleted] = useState(0);
  const [provisionTotal, setProvisionTotal] = useState(0);
  const provisionPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Persist formData across page reloads AND component re-mounts. Any
  // transient re-mount (e.g. wizardRequirements hiccup flipping App.tsx's
  // canProceed) would otherwise zap every selection the user made.
  const WIZARD_FORM_KEY = 'selena-wizard-formdata';
  const [formData, setFormData] = useState<FormData>(() => {
    const defaults: FormData = {
      lang: selectedLanguage,
      wifi: '',
      wifiPassword: '',
      name: t('wizard.defaultHomeName'),
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'Europe/Kyiv',
      stt: 'small',
      tts: 'uk_UA-ukrainian_tts-medium',
      ttsDevice: 'cpu',  // overwritten by GPU auto-detect in step 6
      llmProvider: '',
      llmUrl: 'http://localhost:11434',
      llmApiKey: '',
      llmModel: '',
      username: 'admin',
      pin: '',
      kiosk_name: t('wizard.kioskDefaultName'),
      platformHash: '',
      importSource: '',
    };
    try {
      const saved = localStorage.getItem(WIZARD_FORM_KEY);
      if (saved) {
        const parsed = JSON.parse(saved);
        return { ...defaults, ...parsed, wifiPassword: '', pin: '' }; // never persist secrets
      }
    } catch {}
    return defaults;
  });

  // Write-through every change (debounced via React batching).
  useEffect(() => {
    try {
      // Strip secrets before persisting.
      const safe = { ...formData, wifiPassword: undefined, pin: undefined };
      localStorage.setItem(WIZARD_FORM_KEY, JSON.stringify(safe));
    } catch {}
  }, [formData]);

  // Fresh-install detection. The backend writes a UUID at install time
  // into $CORE_DATA_DIR/.install-id and exposes it via /api/v1/system/info
  // → `install_id`. We cache that value in localStorage. A mismatch means
  // the server was re-installed under a browser / kiosk WebKit that still
  // holds the previous operator's wizard progress; clear it all so the
  // user sees a clean language-select instead of the last step.
  const INSTALL_ID_KEY = 'selena-install-id';
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await fetch('/api/v1/system/info');
        if (!resp.ok) return;
        const info = await resp.json();
        const backendId = String(info?.install_id || '');
        // Prime TTS device toggle from auto-detected GPU presence
        // (start.sh writes hardware.gpu_detected on container boot). User
        // can still flip it in step 6 to save GPU RAM for the LLM.
        const gpuDetected = Boolean(info?.hardware?.gpu_detected);
        const detectedType = String(info?.hardware?.gpu_type || 'none');
        if (!cancelled) {
          setGpuAvailable(gpuDetected);
          setGpuType(detectedType);
          setFormData(prev => prev.ttsDevice === 'cpu' && gpuDetected
            ? { ...prev, ttsDevice: 'gpu' }
            : prev);
        }
        if (!backendId) return;
        const cachedId = localStorage.getItem(INSTALL_ID_KEY);
        if (cachedId && cachedId !== backendId) {
          // Stale progress from a previous install.
          localStorage.removeItem(WIZARD_STEP_KEY);
          localStorage.removeItem(WIZARD_FORM_KEY);
          localStorage.removeItem('selena-setup-stage');
          localStorage.setItem(INSTALL_ID_KEY, backendId);
          if (!cancelled) window.location.reload();
          return;
        }
        if (!cachedId) localStorage.setItem(INSTALL_ID_KEY, backendId);
      } catch { /* ignore */ }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Step 8: QR phone registration state
  const [phoneQrImage, setPhoneQrImage] = useState<string | null>(null);
  const [phoneQrSessionId, setPhoneQrSessionId] = useState<string | null>(null);
  const [phoneQrLoading, setPhoneQrLoading] = useState(false);
  const [phoneQrExpired, setPhoneQrExpired] = useState(false);
  const [phoneRegistered, setPhoneRegistered] = useState(false);
  const [phoneRegisteredName, setPhoneRegisteredName] = useState<string | null>(null);
  const phoneQrPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const phoneQrExpireRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const stopPhoneQrPolling = useCallback(() => {
    if (phoneQrPollRef.current) { clearInterval(phoneQrPollRef.current); phoneQrPollRef.current = null; }
    if (phoneQrExpireRef.current) { clearTimeout(phoneQrExpireRef.current); phoneQrExpireRef.current = null; }
  }, []);

  const startPhoneQrSession = useCallback(async () => {
    stopPhoneQrPolling();
    setPhoneQrLoading(true);
    setPhoneQrExpired(false);
    setPhoneRegistered(false);
    setPhoneRegisteredName(null);
    setPhoneQrImage(null);
    setPhoneQrSessionId(null);
    try {
      const res = await fetch('/api/ui/modules/user-manager/auth/qr/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          mode: 'wizard_setup',
          wizard_username: formData.username,
          wizard_pin: formData.pin,
        }),
      });
      if (!res.ok) throw new Error('Failed to create QR session');
      const data = await res.json();
      setPhoneQrImage(data.qr_image ?? null);
      setPhoneQrSessionId(data.session_id);
      setPhoneQrLoading(false);

      phoneQrPollRef.current = setInterval(async () => {
        try {
          const status = await fetch(`/api/ui/modules/user-manager/auth/qr/status/${data.session_id}`);
          if (status.status === 410) {
            stopPhoneQrPolling();
            setPhoneQrExpired(true);
            return;
          }
          if (!status.ok) return;
          const s = await status.json();
          if (s.status === 'complete') {
            stopPhoneQrPolling();
            setPhoneRegistered(true);
            setPhoneRegisteredName(s.display_name || null);
          }
        } catch { /* network hiccup */ }
      }, 2000);

      phoneQrExpireRef.current = setTimeout(() => {
        stopPhoneQrPolling();
        setPhoneQrExpired(true);
      }, 300_000);
    } catch {
      setPhoneQrLoading(false);
    }
  }, [stopPhoneQrPolling, formData.username, formData.pin]);

  // Start QR session when entering step 8
  useEffect(() => {
    if (step === 9 && !phoneRegistered) {
      startPhoneQrSession();
    }
    return () => { if (step !== 8) stopPhoneQrPolling(); };
  }, [step]); // eslint-disable-line react-hooks/exhaustive-deps

  // Virtual keyboard state
  type KbTarget = 'wifiPassword' | 'name' | 'tzSearch' | 'username' | 'pin' | null;
  const [kbTarget, setKbTarget] = useState<KbTarget>(null);
  const kbInputRefs = useRef<Record<string, HTMLInputElement | null>>({});
  const isTouchDevice = typeof window !== 'undefined' && ('ontouchstart' in window || navigator.maxTouchPoints > 0);
  const [showPassword, setShowPassword] = useState(false);

  const openKb = useCallback((target: KbTarget) => {
    if (isTouchDevice) {
      setKbTarget(target);
      setShowPassword(false);
      // Scroll the input into view above the keyboard
      setTimeout(() => {
        const el = kbInputRefs.current[target ?? ''];
        el?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }, 100);
    }
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
        if (/\d/.test(key))
          setFormData(prev => prev.pin.length < 8 ? ({ ...prev, pin: prev.pin + key }) : prev);
        break;
    }
  }, [kbTarget]);

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

  // Fetch WiFi networks when entering step 2
  useEffect(() => {
    closeKb(); // close keyboard on step change
    if (step === 2) {
      fetchNetworkStatus();
    }
    if (step === 4 && !tzData) fetchTimezones();
    // step 5 (Vosk) and step 6 (Piper) catalogs are fetched by their own
    // useEffects below — they react to filter changes too.
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

      // Surface an existing WiFi connection (host already joined a network
      // outside the wizard). Without this the user is forced to "connect"
      // to a network they are already on.
      if (data.wifi?.connected) {
        setWifiConnected(true);
        if (data.wifi?.ssid) {
          setWifiConnectedSsid(data.wifi.ssid);
          setFormData(prev => ({ ...prev, wifi: data.wifi.ssid }));
        }
      }

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

      // Don't bother scanning if we already have internet — the user is
      // online and the WiFi step is effectively done.
      if (data.wifi?.enabled && !data.internet) {
        fetchWifiNetworks();
      }

      // Auto-start AP only when there's no connectivity at all (no ethernet,
      // no active WiFi connection, AND no internet through any other means).
      if (!data.internet && !data.ethernet?.connected && !data.wifi?.connected && data.wifi?.adapter_found) {
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

  // Online catalogs — no hardcoded fallback. SelenaCore uses Vosk (STT)
  // and Piper (TTS); the catalogs live at /vosk/catalog (alphacephei) and
  // /tts/catalog (Hugging Face piper-voices). Both support filtering by
  // language + quality + free-text search and proper pagination.

  const fetchSttCatalog = async () => {
    setSttLoading(true);
    setSttError(null);
    try {
      // Ask the backend for the ENTIRE catalog up-front (per_page=1000).
      // Upstream Vosk has <100 models total, so the payload is small
      // (~30 KB) and we filter/paginate it locally afterwards.
      const res = await fetch('/api/ui/vosk/catalog?per_page=1000');
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      const raw = Array.isArray(data.models) ? data.models : [];
      setSttRaw(raw);
      if (Array.isArray(data.languages) && data.languages.length) {
        setSttLanguages(data.languages);
      }
      if (!formData.stt || formData.stt === 'small') {
        const active = raw.find((m: any) => m.active);
        const pick = active || raw[0];
        if (pick) setFormData(prev => ({ ...prev, stt: pick.name }));
      }
    } catch (e: any) {
      setSttRaw([]);
      setSttError(e?.message || 'Catalog unreachable');
    } finally {
      setSttLoading(false);
    }
  };

  // Client-side filter + pagination of the raw catalog. Runs on every
  // sttFilter change without any network round-trip, so the language
  // dropdown reacts instantly.
  const sttCatalog = useMemo(() => {
    const q = sttFilter.q.trim().toLowerCase();
    const filtered = sttRaw.filter((m: any) => {
      if (sttFilter.lang && (m.lang || '').toLowerCase() !== sttFilter.lang.toLowerCase()) return false;
      if (sttFilter.quality && (m.type || m.quality || '') !== sttFilter.quality) return false;
      if (q && !(String(m.name || '').toLowerCase().includes(q))) return false;
      return true;
    });
    const start = (sttFilter.page - 1) * sttFilter.per_page;
    return filtered.slice(start, start + sttFilter.per_page);
  }, [sttRaw, sttFilter]);

  const sttPagination = useMemo(() => {
    const q = sttFilter.q.trim().toLowerCase();
    const total = sttRaw.filter((m: any) => {
      if (sttFilter.lang && (m.lang || '').toLowerCase() !== sttFilter.lang.toLowerCase()) return false;
      if (sttFilter.quality && (m.type || m.quality || '') !== sttFilter.quality) return false;
      if (q && !(String(m.name || '').toLowerCase().includes(q))) return false;
      return true;
    }).length;
    return { total, pages: Math.max(1, Math.ceil(total / sttFilter.per_page)) };
  }, [sttRaw, sttFilter]);

  const fetchTtsCatalog = async () => {
    setTtsLoading(true);
    setTtsError(null);
    try {
      // Entire Piper voices.json in one shot (per_page=1000). Backend
      // already caches it 14 days, so after the first wizard run this is
      // a trivial local read.
      const res = await fetch('/api/ui/setup/tts/catalog?per_page=1000');
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      const raw = Array.isArray(data.voices) ? data.voices : [];
      setTtsRaw(raw);
      if (Array.isArray(data.languages) && data.languages.length) {
        setTtsLanguages(data.languages);
      }
      if (Array.isArray(data.qualities) && data.qualities.length) {
        setTtsQualities(data.qualities);
      }
      if (!formData.tts || formData.tts === 'uk_UA-ukrainian_tts-medium') {
        const active = raw.find((v: any) => v.active);
        const pick = active || raw[0];
        if (pick) setFormData(prev => ({ ...prev, tts: pick.id }));
      }
    } catch (e: any) {
      setTtsRaw([]);
      setTtsError(e?.message || 'Catalog unreachable');
    } finally {
      setTtsLoading(false);
    }
  };

  const _ttsFiltered = useMemo(() => {
    const q = ttsFilter.q.trim().toLowerCase();
    return ttsRaw.filter((v: any) => {
      if (ttsFilter.lang && (v.lang || '').toLowerCase() !== ttsFilter.lang.toLowerCase()) return false;
      if (ttsFilter.quality && (v.quality || '') !== ttsFilter.quality) return false;
      if (q && !(String(v.name || v.id || '').toLowerCase().includes(q))) return false;
      return true;
    });
  }, [ttsRaw, ttsFilter]);

  const ttsCatalog = useMemo(() => {
    const start = (ttsFilter.page - 1) * ttsFilter.per_page;
    return _ttsFiltered.slice(start, start + ttsFilter.per_page);
  }, [_ttsFiltered, ttsFilter.page, ttsFilter.per_page]);

  const ttsPagination = useMemo(() => ({
    total: _ttsFiltered.length,
    pages: Math.max(1, Math.ceil(_ttsFiltered.length / ttsFilter.per_page)),
  }), [_ttsFiltered.length, ttsFilter.per_page]);

  // Fetch each catalog ONCE when the user enters the step. Filters and
  // pagination are applied client-side via useMemo, so changing language
  // no longer triggers a network round-trip and the dropdown stays
  // responsive on slow Pi networks.
  useEffect(() => {
    if (step === 5 && sttRaw.length === 0 && !sttLoading) fetchSttCatalog();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step]);

  useEffect(() => {
    if (step === 6 && ttsRaw.length === 0 && !ttsLoading) fetchTtsCatalog();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step]);

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

  const startProvision = async () => {
    setProvisioning(true);
    setProvisionDone(false);
    setProvisionFailed(false);
    setProvisionError(null);
    try {
      const res = await fetch('/api/ui/setup/provision', { method: 'POST' });
      const data = await res.json();
      setProvisionTasks(data.tasks || []);
      setProvisionTotal(data.total || 0);
      setProvisionCompleted(data.completed || 0);
      setProvisionCurrent(data.current_task || '');

      // Start polling
      provisionPollRef.current = setInterval(async () => {
        try {
          const sr = await fetch('/api/ui/setup/provision/status');
          const sd = await sr.json();
          setProvisionTasks(sd.tasks || []);
          setProvisionCurrent(sd.current_task || '');
          setProvisionCompleted(sd.completed || 0);
          setProvisionTotal(sd.total || 0);
          if (sd.done || sd.failed) {
            if (provisionPollRef.current) clearInterval(provisionPollRef.current);
            provisionPollRef.current = null;
            setProvisionDone(sd.done);
            setProvisionFailed(sd.failed);
            if (sd.error) setProvisionError(sd.error);
          }
        } catch { /* poll failed, retry next interval */ }
      }, 1500);
    } catch (e: any) {
      setProvisionFailed(true);
      setProvisionError(e.message || 'Provisioning failed to start');
    }
  };

  // Cleanup poll on unmount
  useEffect(() => {
    return () => {
      if (provisionPollRef.current) clearInterval(provisionPollRef.current);
    };
  }, []);

  // Auto-redirect to dashboard when provisioning completes
  useEffect(() => {
    if (!provisionDone) return;
    const timer = setTimeout(async () => {
      await fetchWizardStatus();
      await fetchWizardRequirements();
    }, 2000);
    return () => clearTimeout(timer);
  }, [provisionDone]);



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
      const body = await resp.json().catch(() => ({}));
      // Step 8: store session_token for wizard browser (temporary session, not persistent device)
      if (step === 9 && body?.session_token) {
        try {
          sessionStorage.setItem('selena_session', body.session_token);
          document.cookie = `selena_device=${body.session_token}; max-age=600; path=/; samesite=strict`;
        } catch { /* ignore storage errors */ }
      }
      if (step === 11) {
        setUser({ name: formData.username, role: 'owner', user_id: null, device_id: null, authenticated: true });
        startProvision();
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
      if (step === 11) {
        setUser({ name: formData.username || 'Admin', role: 'owner', user_id: null, device_id: null, authenticated: true });
        startProvision();
      } else {
        setStep(s => s + 1);
      }
    } catch {
      // skip anyway
      if (step === 11) {
        setUser({ name: formData.username || 'Admin', role: 'owner', user_id: null, device_id: null, authenticated: true });
        startProvision();
      } else {
        setStep(s => s + 1);
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className={cn("h-screen overflow-y-auto bg-zinc-950 text-zinc-50 flex flex-col items-center p-3 pt-4 font-sans transition-[padding]", kbTarget && "pb-52")}>
      <div className="w-full max-w-3xl">

        {/* ── Provisioning Screen ── */}
        {provisioning ? (
          <div className="min-h-[70vh] flex items-center justify-center">
            <ProvisionProgress
              onDone={async () => {
                try {
                  localStorage.removeItem(WIZARD_STEP_KEY);
                  localStorage.removeItem(WIZARD_FORM_KEY);
                  localStorage.removeItem('selena-setup-stage');
                } catch {}
                await fetchWizardStatus();
                await fetchWizardRequirements();
              }}
              onSkip={async () => {
                try {
                  localStorage.removeItem(WIZARD_STEP_KEY);
                  localStorage.removeItem(WIZARD_FORM_KEY);
                  localStorage.removeItem('selena-setup-stage');
                } catch {}
                await fetchWizardStatus();
                await fetchWizardRequirements();
              }}
            />
          </div>
        ) : (
          <>
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
                      {/* v0.4.0: dynamic 16-language list with search, replacing
                          the hardcoded EN/UK pair. LanguagePicker shows the
                          auto-quality warning banner on first auto-language
                          pick, so new users don't get caught off guard by
                          machine-translation artefacts. */}
                      <LanguagePicker
                        currentLang={formData.lang || 'en'}
                        onChange={(code) => {
                          setFormData({ ...formData, lang: code });
                          setSelectedLanguage(code);
                        }}
                      />
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
                            {t('wizard.apOpenUrl')}: <span className="text-cyan-400 font-mono">http://{apIp}</span>
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
                                <div className="relative">
                                  <input
                                    ref={el => { kbInputRefs.current['wifiPassword'] = el; }}
                                    type={showPassword && kbTarget === 'wifiPassword' ? 'text' : 'password'}
                                    value={formData.wifiPassword}
                                    onChange={(e) => { setFormData({ ...formData, wifiPassword: e.target.value }); setWifiConnected(false); }}
                                    onFocus={() => openKb('wifiPassword')}
                                    readOnly={isTouchDevice}
                                    className={cn("w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 pr-9 text-sm text-zinc-50 focus:outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 transition-all", kbTarget === 'wifiPassword' && 'border-emerald-500 ring-1 ring-emerald-500')}
                                    placeholder={t('wizard.wifiPasswordPlaceholder')}
                                  />
                                  {formData.wifiPassword && (
                                    <button
                                      type="button"
                                      onPointerDown={(e) => { e.preventDefault(); setShowPassword(p => !p); }}
                                      className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-zinc-500 hover:text-zinc-300 transition-colors"
                                    >
                                      {showPassword && kbTarget === 'wifiPassword' ? <EyeOff size={14} /> : <Eye size={14} />}
                                    </button>
                                  )}
                                </div>
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

                      {/* Filters */}
                      <div className="flex flex-wrap items-center gap-2">
                        <select
                          value={sttFilter.lang}
                          onChange={(e) => setSttFilter({ ...sttFilter, lang: e.target.value, page: 1 })}
                          className="bg-zinc-950 border border-zinc-800 rounded-lg px-2 py-1.5 text-xs text-zinc-50 focus:outline-none focus:border-emerald-500"
                        >
                          <option value="">{t('wizard.filterAllLanguages')}</option>
                          {sttLanguages.map(l => (
                            <option key={l.code} value={l.code}>{l.label} ({l.count})</option>
                          ))}
                        </select>
                        <select
                          value={sttFilter.quality}
                          onChange={(e) => setSttFilter({ ...sttFilter, quality: e.target.value, page: 1 })}
                          className="bg-zinc-950 border border-zinc-800 rounded-lg px-2 py-1.5 text-xs text-zinc-50 focus:outline-none focus:border-emerald-500"
                        >
                          <option value="">{t('wizard.filterAllQualities')}</option>
                          <option value="small">small</option>
                          <option value="big">big</option>
                        </select>
                        <input
                          type="text"
                          placeholder={t('wizard.filterSearch')}
                          value={sttFilter.q}
                          onChange={(e) => setSttFilter({ ...sttFilter, q: e.target.value, page: 1 })}
                          className="flex-1 min-w-[120px] bg-zinc-950 border border-zinc-800 rounded-lg px-2 py-1.5 text-xs text-zinc-50 focus:outline-none focus:border-emerald-500"
                        />
                        <select
                          value={sttFilter.per_page}
                          onChange={(e) => setSttFilter({ ...sttFilter, per_page: Number(e.target.value), page: 1 })}
                          className="bg-zinc-950 border border-zinc-800 rounded-lg px-2 py-1.5 text-xs text-zinc-50 focus:outline-none focus:border-emerald-500"
                        >
                          {[10, 20, 50, 100].map(n => <option key={n} value={n}>{n}/{t('wizard.perPage')}</option>)}
                        </select>
                      </div>

                      {/* Catalog state */}
                      {sttLoading && (
                        <div className="text-xs text-zinc-500 py-4 text-center">{t('wizard.loadingCatalog')}…</div>
                      )}
                      {sttError && !sttLoading && (
                        <div className="text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">
                          {t('wizard.catalogError')}: {sttError}
                          <button onClick={() => fetchSttCatalog()} className="ml-2 underline">{t('common.retry')}</button>
                        </div>
                      )}

                      {/* Table */}
                      {!sttLoading && !sttError && sttCatalog.length > 0 && (
                        <div className="border border-zinc-800 rounded-lg overflow-hidden">
                          <div className="grid grid-cols-[1fr_60px_60px_70px_24px] gap-2 px-3 py-2 text-[10px] uppercase tracking-wide text-zinc-500 bg-zinc-900 border-b border-zinc-800">
                            <div>{t('wizard.colName')}</div>
                            <div>{t('wizard.colLang')}</div>
                            <div>{t('wizard.colSize')}</div>
                            <div>{t('wizard.colType')}</div>
                            <div></div>
                          </div>
                          <div className="max-h-[280px] overflow-y-auto">
                            {sttCatalog.map((m: any) => {
                              const sizeMb = m.size ? Math.round(m.size / (1024 * 1024)) : (m.size_mb || 0);
                              const selected = formData.stt === m.name;
                              return (
                                <button
                                  key={m.name}
                                  onClick={() => setFormData({ ...formData, stt: m.name })}
                                  className={cn(
                                    "w-full grid grid-cols-[1fr_60px_60px_70px_24px] gap-2 px-3 py-2 text-xs text-left items-center border-b border-zinc-900 last:border-b-0 transition-colors",
                                    selected ? "bg-emerald-500/15 text-emerald-300" : "hover:bg-zinc-900"
                                  )}
                                >
                                  <div className="truncate">
                                    <span className="font-mono">{m.name}</span>
                                    {m.installed && <span className="ml-1.5 text-[9px] px-1 py-0.5 rounded bg-emerald-500/20 text-emerald-400">{t('wizard.sttInstalled')}</span>}
                                  </div>
                                  <div className="text-zinc-400">{(m.lang || '').toUpperCase()}</div>
                                  <div className="text-zinc-400">{sizeMb ? `${sizeMb} MB` : '—'}</div>
                                  <div className="text-zinc-500">{m.type || '—'}</div>
                                  <div>{selected && <Check size={14} className="text-emerald-400" />}</div>
                                </button>
                              );
                            })}
                          </div>
                        </div>
                      )}

                      {/* Pagination */}
                      {!sttLoading && !sttError && sttPagination.total > 0 && (
                        <div className="flex items-center justify-between text-xs text-zinc-500">
                          <div>
                            {t('wizard.paginationTotal', { total: sttPagination.total })}
                          </div>
                          <div className="flex items-center gap-1.5">
                            <button
                              onClick={() => setSttFilter({ ...sttFilter, page: Math.max(1, sttFilter.page - 1) })}
                              disabled={sttFilter.page <= 1}
                              className="px-2 py-1 rounded border border-zinc-800 hover:bg-zinc-900 disabled:opacity-30"
                            >‹</button>
                            <span>{sttFilter.page} / {sttPagination.pages}</span>
                            <button
                              onClick={() => setSttFilter({ ...sttFilter, page: Math.min(sttPagination.pages, sttFilter.page + 1) })}
                              disabled={sttFilter.page >= sttPagination.pages}
                              className="px-2 py-1 rounded border border-zinc-800 hover:bg-zinc-900 disabled:opacity-30"
                            >›</button>
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  {step === 6 && (
                    <div className="space-y-3">
                      <h2 className="text-base font-medium">{t('wizard.ttsTitle')}</h2>
                      <p className="text-zinc-400 text-xs">{t('wizard.ttsDesc')}</p>

                      {/* GPU/CPU toggle — shown only when GPU detected on host */}
                      {gpuAvailable && (
                        <div className="flex items-center gap-3 px-3 py-2 rounded-lg bg-zinc-900 border border-zinc-800">
                          <div className="flex-1">
                            <div className="text-xs text-zinc-200">{t('wizard.ttsDeviceLabel')}</div>
                            <div className="text-[10px] text-zinc-500">
                              {t('wizard.ttsDeviceHint', { type: gpuType })}
                            </div>
                          </div>
                          <div className="flex gap-1">
                            <button
                              type="button"
                              onClick={() => setFormData({ ...formData, ttsDevice: 'gpu' })}
                              className={cn(
                                "px-3 py-1.5 text-xs rounded border transition-colors",
                                formData.ttsDevice === 'gpu'
                                  ? "bg-emerald-500/20 border-emerald-500 text-emerald-300"
                                  : "border-zinc-700 text-zinc-400 hover:bg-zinc-800"
                              )}
                            >GPU</button>
                            <button
                              type="button"
                              onClick={() => setFormData({ ...formData, ttsDevice: 'cpu' })}
                              className={cn(
                                "px-3 py-1.5 text-xs rounded border transition-colors",
                                formData.ttsDevice === 'cpu'
                                  ? "bg-emerald-500/20 border-emerald-500 text-emerald-300"
                                  : "border-zinc-700 text-zinc-400 hover:bg-zinc-800"
                              )}
                            >CPU</button>
                          </div>
                        </div>
                      )}

                      {/* Filters */}
                      <div className="flex flex-wrap items-center gap-2">
                        <select
                          value={ttsFilter.lang}
                          onChange={(e) => setTtsFilter({ ...ttsFilter, lang: e.target.value, page: 1 })}
                          className="bg-zinc-950 border border-zinc-800 rounded-lg px-2 py-1.5 text-xs text-zinc-50 focus:outline-none focus:border-emerald-500"
                        >
                          <option value="">{t('wizard.filterAllLanguages')}</option>
                          {ttsLanguages.map(l => (
                            <option key={l.code} value={l.code}>{l.label} ({l.count})</option>
                          ))}
                        </select>
                        <select
                          value={ttsFilter.quality}
                          onChange={(e) => setTtsFilter({ ...ttsFilter, quality: e.target.value, page: 1 })}
                          className="bg-zinc-950 border border-zinc-800 rounded-lg px-2 py-1.5 text-xs text-zinc-50 focus:outline-none focus:border-emerald-500"
                        >
                          <option value="">{t('wizard.filterAllQualities')}</option>
                          {ttsQualities.map(q => <option key={q} value={q}>{q}</option>)}
                        </select>
                        <input
                          type="text"
                          placeholder={t('wizard.filterSearch')}
                          value={ttsFilter.q}
                          onChange={(e) => setTtsFilter({ ...ttsFilter, q: e.target.value, page: 1 })}
                          className="flex-1 min-w-[120px] bg-zinc-950 border border-zinc-800 rounded-lg px-2 py-1.5 text-xs text-zinc-50 focus:outline-none focus:border-emerald-500"
                        />
                        <select
                          value={ttsFilter.per_page}
                          onChange={(e) => setTtsFilter({ ...ttsFilter, per_page: Number(e.target.value), page: 1 })}
                          className="bg-zinc-950 border border-zinc-800 rounded-lg px-2 py-1.5 text-xs text-zinc-50 focus:outline-none focus:border-emerald-500"
                        >
                          {[10, 20, 50, 100].map(n => <option key={n} value={n}>{n}/{t('wizard.perPage')}</option>)}
                        </select>
                      </div>

                      {/* Catalog state */}
                      {ttsLoading && (
                        <div className="text-xs text-zinc-500 py-4 text-center">{t('wizard.loadingCatalog')}…</div>
                      )}
                      {ttsError && !ttsLoading && (
                        <div className="text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">
                          {t('wizard.catalogError')}: {ttsError}
                          <button onClick={() => fetchTtsCatalog()} className="ml-2 underline">{t('common.retry')}</button>
                        </div>
                      )}

                      {/* Table */}
                      {!ttsLoading && !ttsError && ttsCatalog.length > 0 && (
                        <div className="border border-zinc-800 rounded-lg overflow-hidden">
                          <div className="grid grid-cols-[1fr_60px_60px_70px_36px_24px] gap-2 px-3 py-2 text-[10px] uppercase tracking-wide text-zinc-500 bg-zinc-900 border-b border-zinc-800">
                            <div>{t('wizard.colName')}</div>
                            <div>{t('wizard.colLang')}</div>
                            <div>{t('wizard.colSize')}</div>
                            <div>{t('wizard.colQuality')}</div>
                            <div></div>
                            <div></div>
                          </div>
                          <div className="max-h-[280px] overflow-y-auto">
                            {ttsCatalog.map((v: any) => {
                              const selected = formData.tts === v.id;
                              return (
                                <div
                                  key={v.id}
                                  className={cn(
                                    "grid grid-cols-[1fr_60px_60px_70px_36px_24px] gap-2 px-3 py-2 text-xs items-center border-b border-zinc-900 last:border-b-0 transition-colors",
                                    selected ? "bg-emerald-500/15" : "hover:bg-zinc-900"
                                  )}
                                >
                                  <button
                                    onClick={() => setFormData({ ...formData, tts: v.id })}
                                    className={cn("text-left truncate", selected && "text-emerald-300")}
                                  >
                                    <span className="font-mono">{v.id}</span>
                                    {v.installed && <span className="ml-1.5 text-[9px] px-1 py-0.5 rounded bg-emerald-500/20 text-emerald-400">{t('wizard.sttInstalled')}</span>}
                                  </button>
                                  <div className="text-zinc-400">{(v.lang || '').toUpperCase()}</div>
                                  <div className="text-zinc-400">{v.size_mb ? `${v.size_mb} MB` : '—'}</div>
                                  <div className="text-zinc-500">{v.quality || '—'}</div>
                                  <button
                                    onClick={(e) => { e.stopPropagation(); previewVoice(v.id); }}
                                    disabled={previewingVoice !== null}
                                    title={t('wizard.ttsPreview')}
                                    className="flex items-center justify-center text-zinc-400 hover:text-emerald-400 disabled:opacity-50"
                                  >
                                    <Play size={12} className={previewingVoice === v.id ? 'animate-pulse text-emerald-500' : ''} />
                                  </button>
                                  <div>{selected && <Check size={14} className="text-emerald-400" />}</div>
                                </div>
                              );
                            })}
                          </div>
                        </div>
                      )}

                      {/* Pagination */}
                      {!ttsLoading && !ttsError && ttsPagination.total > 0 && (
                        <div className="flex items-center justify-between text-xs text-zinc-500">
                          <div>
                            {t('wizard.paginationTotal', { total: ttsPagination.total })}
                          </div>
                          <div className="flex items-center gap-1.5">
                            <button
                              onClick={() => setTtsFilter({ ...ttsFilter, page: Math.max(1, ttsFilter.page - 1) })}
                              disabled={ttsFilter.page <= 1}
                              className="px-2 py-1 rounded border border-zinc-800 hover:bg-zinc-900 disabled:opacity-30"
                            >‹</button>
                            <span>{ttsFilter.page} / {ttsPagination.pages}</span>
                            <button
                              onClick={() => setTtsFilter({ ...ttsFilter, page: Math.min(ttsPagination.pages, ttsFilter.page + 1) })}
                              disabled={ttsFilter.page >= ttsPagination.pages}
                              className="px-2 py-1 rounded border border-zinc-800 hover:bg-zinc-900 disabled:opacity-30"
                            >›</button>
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  {step === 7 && (
                    <LlmProviderStep
                      formData={formData}
                      setFormData={setFormData}
                    />
                  )}

                  {step === 8 && (
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
                          <div className="relative">
                            <input
                              ref={el => { kbInputRefs.current['pin'] = el; }}
                              type={showPassword && kbTarget === 'pin' ? 'text' : 'password'}
                              maxLength={8}
                              value={formData.pin}
                              onChange={(e) => setFormData({ ...formData, pin: e.target.value.replace(/\D/g, '') })}
                              onFocus={() => openKb('pin')}
                              readOnly={isTouchDevice}
                              className={cn("w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 pr-9 text-sm text-zinc-50 focus:outline-none focus:border-emerald-500 transition-all tracking-widest font-mono", kbTarget === 'pin' && 'border-emerald-500 ring-1 ring-emerald-500')}
                              placeholder={t('wizard.userPinPlaceholder')}
                            />
                            {formData.pin && (
                              <button
                                type="button"
                                onPointerDown={(e) => { e.preventDefault(); setShowPassword(p => !p); }}
                                className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-zinc-500 hover:text-zinc-300 transition-colors"
                              >
                                {showPassword && kbTarget === 'pin' ? <EyeOff size={14} /> : <Eye size={14} />}
                              </button>
                            )}
                          </div>
                          <p className="mt-1 text-[11px] text-amber-400/80">
                            {t('wizard.userPinRequired')}
                          </p>
                        </div>
                      </div>
                    </div>
                  )}

                  {step === 9 && (
                    <div className="space-y-4">
                      <h2 className="text-base font-medium">{t('wizard.homeDevicesTitle')}</h2>
                      <p className="text-zinc-400 text-xs">{t('wizard.homeDevicesDesc')}</p>

                      {/* Kiosk screen name */}
                      <div className="p-3 rounded-lg bg-zinc-900 border border-zinc-800">
                        <div className="flex items-center gap-2 mb-2">
                          <Smartphone size={14} className="text-violet-400" />
                          <span className="text-xs font-medium text-zinc-300">{t('wizard.homeDevicesScreen')}</span>
                        </div>
                        <p className="text-xs text-zinc-500 mb-2">{t('wizard.homeDevicesScreenDesc')}</p>
                        <input
                          type="text"
                          value={formData.kiosk_name}
                          onChange={(e) => setFormData({ ...formData, kiosk_name: e.target.value })}
                          className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-zinc-50 focus:outline-none focus:border-violet-500 transition-all"
                          placeholder={t('wizard.homeDevicesScreenPlaceholder')}
                        />
                      </div>

                      {/* QR code for phone registration */}
                      <div className="p-3 rounded-lg bg-zinc-900 border border-zinc-800">
                        <div className="flex items-center gap-2 mb-2">
                          <QrCode size={14} className="text-emerald-400" />
                          <span className="text-xs font-medium text-zinc-300">{t('wizard.homeDevicesMobile')}</span>
                        </div>
                        <p className="text-xs text-zinc-500 mb-3">{t('wizard.homeDevicesMobileDesc')}</p>

                        {phoneRegistered ? (
                          <div className="flex flex-col items-center gap-2 py-4">
                            <div className="w-12 h-12 rounded-full bg-emerald-500/20 flex items-center justify-center">
                              <Check size={24} className="text-emerald-400" />
                            </div>
                            <span className="text-sm font-medium text-emerald-400">{t('wizard.phoneRegistered')}</span>
                            {phoneRegisteredName && (
                              <span className="text-xs text-zinc-400">{phoneRegisteredName}</span>
                            )}
                          </div>
                        ) : phoneQrExpired ? (
                          <div className="flex flex-col items-center gap-3 py-4">
                            <span className="text-xs text-zinc-500">{t('wizard.qrExpired')}</span>
                            <button
                              onClick={startPhoneQrSession}
                              className="px-3 py-1.5 rounded-lg text-xs font-medium bg-violet-600 hover:bg-violet-500 text-white transition-colors flex items-center gap-1.5"
                            >
                              <RefreshCw size={12} />
                              {t('wizard.qrRefresh')}
                            </button>
                          </div>
                        ) : phoneQrLoading ? (
                          <div className="flex justify-center py-6">
                            <RefreshCw size={20} className="text-zinc-500 animate-spin" />
                          </div>
                        ) : phoneQrImage ? (
                          <div className="flex flex-col items-center gap-2">
                            <div className="bg-white rounded-lg p-2">
                              <img src={phoneQrImage} alt="QR" className="w-40 h-40" />
                            </div>
                            <span className="text-[10px] text-zinc-500 text-center">{t('wizard.phoneQrHint')}</span>
                          </div>
                        ) : null}
                      </div>
                    </div>
                  )}

                  {step === 10 && (
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

                  {step === 11 && (
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
                    {(step === 9 || step === 10 || step === 11) && (
                      <button
                        onClick={skipStep}
                        disabled={submitting}
                        className="px-4 py-2 rounded-lg text-xs font-medium text-zinc-400 hover:text-zinc-50 hover:bg-zinc-800 transition-colors disabled:opacity-50"
                      >
                        {t('common.skip')}
                      </button>
                    )}
                    {step === 2 && hasInternet && (
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
                        (step === 2 && !hasInternet && !(wifiConnected && wifiConnectedSsid === formData.wifi)) ||
                        (step === 8 && (!formData.username || formData.pin.length < 4))
                      }
                      className="px-5 py-2 rounded-lg text-xs font-medium bg-emerald-500 text-zinc-950 hover:bg-emerald-400 transition-colors flex items-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed min-w-[80px] justify-center"
                    >
                      {submitting ? (
                        <div className="w-4 h-4 border-2 border-zinc-950 border-t-transparent rounded-full animate-spin" />
                      ) : (
                        <>
                          {step === 11 ? t('common.finish') : t('common.next')}
                          {step !== 10 && <ChevronRight size={16} />}
                        </>
                      )}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </>
        )}
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
        inputValue={kbTarget === 'wifiPassword' ? formData.wifiPassword : kbTarget === 'pin' ? formData.pin : kbTarget === 'name' ? formData.name : kbTarget === 'username' ? formData.username : kbTarget === 'tzSearch' ? tzSearch : ''}
        isPassword={kbTarget === 'wifiPassword' || kbTarget === 'pin'}
        passwordVisible={showPassword}
        onToggleVisibility={() => setShowPassword(p => !p)}
      />
    </div>
  );
}
