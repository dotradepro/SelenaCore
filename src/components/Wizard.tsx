import { useState, useEffect, useRef, useCallback } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { useTranslation } from 'react-i18next';
import { useStore } from '../store/useStore';
import { Check, ChevronRight, Wifi, Globe, Mic, User, Cloud, Download, Activity, AlertCircle, RefreshCw, Signal, Lock, Play, WifiOff, Cable, Radio, Eye, EyeOff, Smartphone, QrCode } from 'lucide-react';
import { cn } from '../lib/utils';
import VirtualKeyboard from './VirtualKeyboard';

function HomeIcon(props: any) {
  return <svg {...props} xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /><polyline points="9 22 9 12 15 12 15 22" /></svg>;
}

const STEP_ICONS = [Globe, Wifi, HomeIcon, Globe, Mic, Mic, User, Smartphone, Cloud, Download];

// Map frontend step number to backend step name + data builder
const STEP_MAP: Record<number, { name: string; buildData: (f: FormData) => Record<string, string> }> = {
  1: { name: 'language', buildData: (f) => ({ language: f.lang }) },
  2: { name: 'wifi', buildData: (f) => ({ ssid: f.wifi, password: f.wifiPassword }) },
  3: { name: 'device_name', buildData: (f) => ({ name: f.name }) },
  4: { name: 'timezone', buildData: (f) => ({ timezone: f.timezone }) },
  5: { name: 'stt_model', buildData: (f) => ({ model: f.stt }) },
  6: { name: 'tts_voice', buildData: (f) => ({ voice: f.tts }) },
  7: { name: 'admin_user', buildData: (f) => ({ username: f.username, pin: f.pin }) },
  8: { name: 'home_devices', buildData: (f) => ({ device_name: f.kiosk_name }) },
  9: { name: 'platform', buildData: (f) => ({ device_hash: f.platformHash }) },
  10: { name: 'import', buildData: (f) => ({ source: f.importSource }) },
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
  const [step, setStep] = useState(1);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
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

  const [formData, setFormData] = useState<FormData>({
    lang: selectedLanguage,
    wifi: '',
    wifiPassword: '',
    name: t('wizard.defaultHomeName'),
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'Europe/Kyiv',
    stt: 'small',
    tts: 'uk_UA-ukrainian_tts-medium',
    username: 'admin',
    pin: '',
    kiosk_name: t('wizard.kioskDefaultName'),
    platformHash: '',
    importSource: '',
  });

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
    if (step === 8 && !phoneRegistered) {
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

  const fetchSttModels = async () => {
    try {
      // SelenaCore uses Vosk for STT (see core/stt/factory.py). The real
      // catalog comes from /vosk/catalog (cached from alphacephei.com), not
      // a non-existent /stt/models — that older URL would 404 and we'd fall
      // through to a stale Whisper fallback.
      const res = await fetch('/api/ui/setup/vosk/catalog?per_page=50');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const raw = Array.isArray(data.models) ? data.models : [];
      // Map Vosk catalog rows to the SttModel shape this component expects.
      const mapped: SttModel[] = raw.map((m: any) => {
        const sizeBytes = typeof m.size === 'number' ? m.size : 0;
        const sizeMb = sizeBytes ? Math.round(sizeBytes / (1024 * 1024)) : 0;
        const isBig = (m.type || '').toLowerCase() === 'big' || sizeMb > 200;
        return {
          id: m.name,
          name: m.name,
          lang: (m.lang || 'auto').slice(0, 2),
          ram_mb: 0,
          size_mb: sizeMb,
          quality: isBig ? 'high' : 'ok',
          installed: !!m.installed,
          active: !!m.active,
          fits_ram: true,
        };
      });
      setSttModels(mapped);
      const activeModel = mapped.find(m => m.active);
      if (activeModel) setFormData(prev => ({ ...prev, stt: activeModel.id }));
      else if (mapped.length > 0) setFormData(prev => ({ ...prev, stt: mapped[0].id }));
    } catch {
      // Catalog unreachable — fall through to hardcoded Vosk defaults below
    }
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
      if (step === 8 && body?.session_token) {
        try {
          sessionStorage.setItem('selena_session', body.session_token);
          document.cookie = `selena_device=${body.session_token}; max-age=600; path=/; samesite=strict`;
        } catch { /* ignore storage errors */ }
      }
      if (step === 10) {
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
      if (step === 10) {
        setUser({ name: formData.username || 'Admin', role: 'owner', user_id: null, device_id: null, authenticated: true });
        startProvision();
      } else {
        setStep(s => s + 1);
      }
    } catch {
      // skip anyway
      if (step === 10) {
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
          <div className="flex flex-col items-center justify-center min-h-[70vh] gap-6">
            {/* Animated spinner ring */}
            <div className="relative w-24 h-24">
              <svg className="w-full h-full animate-spin" viewBox="0 0 100 100">
                <circle cx="50" cy="50" r="42" fill="none" stroke="#27272a" strokeWidth="6" />
                <circle cx="50" cy="50" r="42" fill="none" stroke="#10b981" strokeWidth="6"
                  strokeLinecap="round"
                  strokeDasharray={`${provisionTotal > 0 ? (provisionCompleted / provisionTotal) * 264 : 30} 264`}
                  className="transition-all duration-700"
                  style={{ transformOrigin: 'center', transform: 'rotate(-90deg)' }}
                />
              </svg>
              <div className="absolute inset-0 flex items-center justify-center">
                {provisionDone ? (
                  <Check size={32} className="text-emerald-500" />
                ) : provisionFailed ? (
                  <AlertCircle size={32} className="text-red-400" />
                ) : (
                  <span className="text-sm font-bold text-emerald-500">
                    {provisionTotal > 0 ? `${provisionCompleted}/${provisionTotal}` : '…'}
                  </span>
                )}
              </div>
            </div>

            {/* Title */}
            <div className="text-center">
              <h2 className="text-lg font-semibold mb-1">
                {provisionDone ? t('wizard.provisionDone') : provisionFailed ? t('wizard.provisionFailed') : t('wizard.provisionTitle')}
              </h2>
              <p className="text-xs text-zinc-400">
                {provisionDone ? t('wizard.provisionDoneDesc') : provisionFailed ? (provisionError || t('wizard.provisionFailedDesc')) : t('wizard.provisionDesc')}
              </p>
            </div>

            {/* Task list */}
            <div className="w-full max-w-sm space-y-2">
              {provisionTasks.map((task) => (
                <div
                  key={task.id}
                  className={cn(
                    "flex items-center gap-3 px-3 py-2.5 rounded-lg border transition-all",
                    task.status === 'running' ? "border-emerald-500/50 bg-emerald-500/5" :
                      task.status === 'done' ? "border-zinc-800 bg-zinc-900/50 opacity-70" :
                        task.status === 'error' ? "border-red-500/30 bg-red-500/5" :
                          "border-zinc-800/50 bg-zinc-900/30 opacity-40"
                  )}
                >
                  <div className="shrink-0 w-5 h-5 flex items-center justify-center">
                    {task.status === 'running' ? (
                      <div className="w-4 h-4 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin" />
                    ) : task.status === 'done' ? (
                      <Check size={16} className="text-emerald-500" />
                    ) : task.status === 'error' ? (
                      <AlertCircle size={14} className="text-red-400" />
                    ) : (
                      <div className="w-2 h-2 rounded-full bg-zinc-600" />
                    )}
                  </div>
                  <div className="flex-1 min-w-0">
                    <span className={cn("text-sm font-medium", task.status === 'running' && "text-emerald-400")}>
                      {t(`wizard.provTask_${task.label}` as any)}
                    </span>
                    {task.error && <p className="text-[10px] text-red-400 mt-0.5 truncate">{task.error}</p>}
                  </div>
                </div>
              ))}
            </div>

            {/* Done / Retry buttons */}
            {provisionDone && (
              <motion.button
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                onClick={async () => { await fetchWizardStatus(); await fetchWizardRequirements(); }}
                className="px-6 py-2.5 rounded-lg text-sm font-medium bg-emerald-500 text-zinc-950 hover:bg-emerald-400 transition-colors flex items-center gap-2"
              >
                {t('wizard.provisionContinue')}
                <ChevronRight size={16} />
              </motion.button>
            )}
            {provisionFailed && (
              <div className="flex gap-3">
                <button
                  onClick={() => startProvision()}
                  className="px-5 py-2 rounded-lg text-xs font-medium bg-zinc-800 text-zinc-50 hover:bg-zinc-700 transition-colors flex items-center gap-1.5"
                >
                  <RefreshCw size={14} />
                  {t('wizard.provisionRetry')}
                </button>
                <button
                  onClick={async () => { await fetchWizardStatus(); await fetchWizardRequirements(); }}
                  className="px-5 py-2 rounded-lg text-xs font-medium text-zinc-400 hover:text-zinc-50 hover:bg-zinc-800 transition-colors"
                >
                  {t('common.skip')}
                </button>
              </div>
            )}
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
                      {sttRamInfo.available > 0 && (
                        <div className="text-xs text-zinc-500">
                          RAM: {sttRamInfo.available} MB {t('wizard.sttAvailable')} / {sttRamInfo.total} MB {t('wizard.sttTotal')}
                        </div>
                      )}
                      <div className="space-y-2">
                        {(sttModels.length > 0 ? sttModels : [
                          { id: 'vosk-model-small-en-us-0.15', name: 'vosk-model-small-en-us-0.15', lang: 'en', ram_mb: 0, size_mb: 40,   quality: 'ok',   installed: false, active: false, fits_ram: true },
                          { id: 'vosk-model-en-us-0.22',       name: 'vosk-model-en-us-0.22',       lang: 'en', ram_mb: 0, size_mb: 1800, quality: 'high', installed: false, active: false, fits_ram: true },
                          { id: 'vosk-model-small-uk-v3-small',name: 'vosk-model-small-uk-v3-small',lang: 'uk', ram_mb: 0, size_mb: 75,   quality: 'ok',   installed: false, active: false, fits_ram: true },
                          { id: 'vosk-model-uk-v3',            name: 'vosk-model-uk-v3',            lang: 'uk', ram_mb: 0, size_mb: 350,  quality: 'high', installed: false, active: false, fits_ram: true },
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
                                <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-zinc-800 text-zinc-400">{m.size_mb} MB</span>
                                {m.installed && <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-500">{t('wizard.sttInstalled')}</span>}
                                {!m.fits_ram && <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-red-500/10 text-red-400">{t('wizard.sttNoRam')}</span>}
                              </div>
                              <div className="text-xs text-zinc-400 mt-0.5">
                                {m.quality === 'high' ? t('wizard.sttLarge' + m.lang.charAt(0).toUpperCase() + m.lang.slice(1)) : t('wizard.sttSmall' + m.lang.charAt(0).toUpperCase() + m.lang.slice(1))}
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
                          { id: 'uk_UA-ukrainian_tts-medium', name: 'Tetiana', language: 'uk', gender: 'female', size_mb: 50, installed: false, active: false },
                          { id: 'uk_UA-lada-x_low', name: 'Lada', language: 'uk', gender: 'female', size_mb: 21, installed: false, active: false },
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

                  {step === 8 && (
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

                  {step === 9 && (
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

                  {step === 10 && (
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
                    {(step === 8 || step === 9 || step === 10) && (
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
                        (step === 7 && (!formData.username || formData.pin.length < 4))
                      }
                      className="px-5 py-2 rounded-lg text-xs font-medium bg-emerald-500 text-zinc-950 hover:bg-emerald-400 transition-colors flex items-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed min-w-[80px] justify-center"
                    >
                      {submitting ? (
                        <div className="w-4 h-4 border-2 border-zinc-950 border-t-transparent rounded-full animate-spin" />
                      ) : (
                        <>
                          {step === 10 ? t('common.finish') : t('common.next')}
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
