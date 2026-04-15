import { create } from 'zustand';
import { changeLanguage } from '../i18n/i18n';

export interface Device {
  device_id: string;
  name: string;
  type: string;
  protocol: string;
  state: Record<string, unknown>;
  capabilities: string[];
  last_seen: number | null;
  module_id: string | null;
  meta: Record<string, unknown>;
  entity_type?: string | null;
  location?: string | null;
  keywords_user?: string[];
  keywords_en?: string[];
}

export interface Module {
  name: string;
  version: string;
  type: string;
  status: string;
  runtime_mode: string;
  port: number;
  installed_at: number;
  ui?: {
    icon?: string;
    widget?: { file?: string; size?: string; min_size?: string; max_size?: string };
    settings?: string;
  };
}

export interface CloudProviderStatus {
  id: string;
  name: string;
  configured: boolean;
  model: string;
  active: boolean;
}

export interface LlmEngineStatus {
  provider: string;
  model: string;
  twoStep: boolean;
  cloudProviders: CloudProviderStatus[];
  intentCache: { size: number; hot: number };
}

export interface NativeService {
  name: string;
  running: boolean;
  url: string | null;
  extra: Record<string, unknown>;
}

export interface SystemStats {
  cpuTemp: number;
  cpuLoad: [number, number, number];
  cpuCount: number;
  ramUsedMb: number;
  ramTotalMb: number;
  swapUsedMb: number;
  swapTotalMb: number;
  diskUsedGb: number;
  diskTotalGb: number;
  uptime: number;
  integrity: string;
  mode: string;
  version: string;
  corePort: number;
  ollama: {
    installed: boolean;
    running: boolean;
    model: string | null;
    modelLoaded: boolean;
    loadedModel?: string;
    models?: { name: string; size_mb: number }[];
  };
  llmEngine: LlmEngineStatus;
  nativeServices: NativeService[];
}

export interface Health {
  status: string;
  version: string;
  mode: string;
  uptime: number;
  integrity: string;
}

export interface StepStatus {
  required: boolean;
  done: boolean;
  label: string;
}

export interface WizardRequirements {
  can_proceed: boolean;
  wizard_completed: boolean;
  steps: Record<string, StepStatus>;
}

// ── Widget layout — persisted to backend (synced across all browsers/kiosk) ──
export interface WidgetLayout {
  pinned: string[];
  sizes: Record<string, 'compact' | 'normal'>;
  hidden: string[];
  screens: number;
  positions: Record<string, number>; // widget name -> absolute slot index (screen * PER_SCREEN + slot)
  spans: Record<string, { cols: number; rows: number }>; // widget name -> grid span
}
function loadWidgetLayout(): WidgetLayout {
  // Initial value from localStorage (fast, synchronous); will be overridden
  // by the backend value once connectSyncStream() fetches it.
  try {
    const raw = localStorage.getItem('selena-widget-layout');
    if (raw) return { hidden: [], screens: 1, positions: {}, spans: {}, ...JSON.parse(raw) } as WidgetLayout;
  } catch { /* ignore */ }
  return { pinned: [], sizes: {}, hidden: [], screens: 1, positions: {}, spans: {} };
}
function saveWidgetLayout(layout: WidgetLayout) {
  // Mirror to localStorage for instant rehydration on next page load
  try { localStorage.setItem('selena-widget-layout', JSON.stringify(layout)); } catch { /* ignore */ }
  // Persist to backend (async, no await — fire-and-forget)
  fetch('/api/ui/layout', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(layout),
  }).catch(() => { /* ignore network errors */ });
}

export interface Toast {
  message: string;
  type: 'success' | 'error' | 'info';
}

export type ThemeMode = 'auto' | 'light' | 'dark';

function loadTheme(): ThemeMode {
  const stored = localStorage.getItem('selena-theme');
  if (stored === 'light' || stored === 'dark' || stored === 'auto') return stored;
  return 'auto';
}

function applyThemeClass(mode: ThemeMode) {
  const root = document.documentElement;
  if (mode === 'auto') {
    const h = new Date().getHours();
    root.classList.toggle('light', h >= 7 && h < 20);
  } else {
    root.classList.toggle('light', mode === 'light');
  }
}

function broadcastThemeToIframes() {
  document.querySelectorAll('iframe').forEach(f => {
    try { f.contentWindow?.postMessage({ type: 'theme_changed' }, '*'); } catch (_) { /* cross-origin */ }
  });
}

// ── Custom Themes + Wallpapers ────────────────────────────────────────────────

export interface CustomTheme {
  id: string;
  name: { en: string; uk: string };
  builtIn: boolean;
  dark: Record<string, string>;
  light: Record<string, string>;
}

export interface WallpaperInfo {
  id: string;
  filename: string;
  url: string;
}

function applyCustomThemeCSS(theme: CustomTheme | null) {
  const existing = document.getElementById('selena-custom-theme');
  if (!theme || theme.id === 'default') {
    existing?.remove();
    return;
  }
  const style = existing ?? Object.assign(document.createElement('style'), { id: 'selena-custom-theme' });
  const darkProps = Object.entries(theme.dark).map(([k, v]) => `--${k}:${v}`).join(';');
  const lightProps = Object.entries(theme.light).map(([k, v]) => `--${k}:${v}`).join(';');
  let css = '';
  if (darkProps) css += `:root{${darkProps}}`;
  if (lightProps) css += `:root.light{${lightProps}}`;
  style.textContent = css;
  if (!existing) document.head.appendChild(style);
}

function applyWallpaperClass(hasWallpaper: boolean) {
  document.documentElement.classList.toggle('has-wallpaper', hasWallpaper);
}

function broadcastThemeVarsToIframes() {
  document.querySelectorAll('iframe').forEach(f => {
    try { f.contentWindow?.postMessage({ type: 'theme_vars_changed' }, '*'); } catch (_) { /* cross-origin */ }
  });
}

export interface AuthUser {
  name: string;
  role: string;
  user_id: string | null;
  device_id: string | null;
  authenticated: boolean;
}

interface AppState {
  isConfigured: boolean;
  wizardLoading: boolean;
  setupStage: 'landing' | 'wizard';
  selectedLanguage: string;
  theme: ThemeMode;
  setTheme: (mode: ThemeMode) => void;
  initThemeListener: () => () => void;
  wizardRequirements: WizardRequirements | null;
  user: AuthUser | null;
  authLoading: boolean;
  elevatedToken: string | null;
  health: Health | null;
  stats: SystemStats | null;
  devices: Device[];
  modules: Module[];
  devicesLoading: boolean;
  modulesLoading: boolean;
  setConfigured: (status: boolean) => void;
  setUser: (user: AuthUser) => void;
  setElevatedToken: (token: string | null) => void;
  initAuth: () => Promise<void>;
  setSetupStage: (stage: 'landing' | 'wizard') => void;
  setSelectedLanguage: (lang: string) => void;
  fetchWizardStatus: () => Promise<void>;
  fetchWizardRequirements: () => Promise<void>;

  fetchStats: () => Promise<void>;
  fetchDevices: () => Promise<void>;
  fetchModules: () => Promise<void>;
  stopModule: (name: string) => Promise<void>;
  startModule: (name: string) => Promise<void>;
  removeModule: (name: string) => Promise<void>;
  updateDeviceState: (deviceId: string, state: Record<string, unknown>) => Promise<void>;
  voiceStatus: 'idle' | 'listening' | 'speaking';
  setVoiceStatus: (status: 'idle' | 'listening' | 'speaking') => void;
  widgetLayout: WidgetLayout;
  initWidgetLayout: (modules: Module[]) => void;
  pinModule: (name: string, preferredSlot?: number) => void;
  unpinModule: (name: string) => void;
  setWidgetSize: (name: string, size: 'compact' | 'normal') => void;

  swapWidgets: (nameA: string, nameB: string) => void;
  moveWidgetToSlot: (name: string, slot: number) => void;
  setWidgetSpan: (name: string, cols: number, rows: number) => void;
  addScreen: () => void;
  connectSyncStream: () => () => void;
  lastServerContact: number;
  toast: Toast | null;
  showToast: (message: string, type?: 'success' | 'error' | 'info') => void;

  // Custom Themes
  customThemes: CustomTheme[];
  activeThemeId: string;
  fetchThemes: () => Promise<void>;
  activateTheme: (id: string) => Promise<void>;
  createTheme: (name: { en: string; uk: string }, dark: Record<string, string>, light: Record<string, string>) => Promise<CustomTheme | null>;
  updateTheme: (id: string, name: { en: string; uk: string }, dark: Record<string, string>, light: Record<string, string>) => Promise<void>;
  deleteTheme: (id: string) => Promise<void>;

  // Wallpapers
  wallpapers: WallpaperInfo[];
  activeWallpaper: string | null;
  wallpaperBlur: number;
  wallpaperOpacity: number;
  fetchWallpapers: () => Promise<void>;
  setWallpaper: (filename: string | null, blur?: number, opacity?: number) => Promise<void>;
}

// ── Request optimizations ─────────────────────────────────────────────────────
// 1. In-flight dedup: parallel GET calls for the same URL share one request.
// 2. TTL cache: wizard/requirements-style endpoints that rarely change.
// 3. Module-fetch dedup: SSE events skip fetchModules if a mutation just ran it.

const _inflight = new Map<string, Promise<unknown>>();
const _cache = new Map<string, { data: unknown; ts: number }>();

const CACHE_TTL: Record<string, number> = {
  '/api/ui/wizard/status': 30_000,
  '/api/ui/wizard/requirements': 60_000,
};

async function apiFetch(path: string, opts?: RequestInit): Promise<unknown> {
  const isGet = !opts?.method || opts.method.toUpperCase() === 'GET';
  const ttl = isGet ? CACHE_TTL[path] : undefined;

  // Serve from TTL cache if still fresh
  if (ttl) {
    const hit = _cache.get(path);
    if (hit && Date.now() - hit.ts < ttl) return hit.data;
  }

  // Reuse an already-in-flight GET request (dedup parallel mounts)
  if (isGet && _inflight.has(path)) return _inflight.get(path)!;

  const promise = (async () => {
    const res = await fetch(path, opts);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    if (res.status === 204) return null;
    const data = await res.json();
    if (ttl) _cache.set(path, { data, ts: Date.now() });
    return data;
  })().finally(() => _inflight.delete(path));

  if (isGet) _inflight.set(path, promise);
  return promise;
}

export const useStore = create<AppState>((set, get) => ({
  isConfigured: false,
  wizardLoading: true,
  // Persist setupStage so a kiosk reload mid-wizard lands back on the
  // wizard instead of bouncing the user to LanguageSelect → lose state.
  setupStage: ((): 'landing' | 'wizard' => {
    try {
      const saved = localStorage.getItem('selena-setup-stage');
      return saved === 'wizard' ? 'wizard' : 'landing';
    } catch { return 'landing'; }
  })(),
  selectedLanguage: localStorage.getItem('selena-lang') || 'en',
  theme: loadTheme(),
  wizardRequirements: null,
  user: null,
  authLoading: true,
  elevatedToken: null,
  health: null,
  stats: null,
  devices: [],
  modules: [],
  devicesLoading: false,
  modulesLoading: false,
  voiceStatus: 'idle' as 'idle' | 'listening' | 'speaking',

  setConfigured: (status) => set({ isConfigured: status }),
  setUser: (user) => set({ user }),
  setElevatedToken: (token) => {
    set({ elevatedToken: token });
    try {
      if (token) sessionStorage.setItem('selena_elevated', token);
      else sessionStorage.removeItem('selena_elevated');
    } catch { /* ignore */ }
  },
  initAuth: async () => {
    set({ authLoading: true });
    // Restore elevated token from sessionStorage (survives page refresh)
    try {
      const saved = sessionStorage.getItem('selena_elevated');
      if (saved) set({ elevatedToken: saved });
    } catch { /* ignore */ }

    // Find device token: session token first, then cookie/localStorage
    let token: string | null = null;
    try {
      // Temporary QR session token (highest priority)
      token = sessionStorage.getItem('selena_session');
      if (!token) {
        const cookieMatch = document.cookie.match(/(?:^|;\s*)selena_device=([^;]+)/);
        token = cookieMatch?.[1] ?? localStorage.getItem('selena_device');
      }
    } catch { /* ignore */ }

    if (!token) {
      set({ user: { name: 'Guest', role: 'guest', user_id: null, device_id: null, authenticated: false }, authLoading: false });
      return;
    }

    try {
      const res = await fetch('/api/ui/modules/user-manager/me', {
        headers: { 'X-Device-Token': token },
        credentials: 'include',
      });
      if (res.ok) {
        const data = await res.json();
        set({
          user: {
            name: data.display_name || 'User',
            role: data.role ?? 'guest',
            user_id: data.user_id ?? null,
            device_id: data.device_id ?? null,
            authenticated: data.authenticated ?? true,
          },
          authLoading: false,
        });
        return;
      }
      // If 401 and we had a session token, it expired — clear it
      if (res.status === 401) {
        try {
          sessionStorage.removeItem('selena_session');
          localStorage.removeItem('selena_device');
          document.cookie = 'selena_device=; max-age=0; path=/';
        } catch { /* ignore */ }
      }
    } catch { /* network error — fall through to guest */ }

    set({ user: { name: 'Guest', role: 'guest', user_id: null, device_id: null, authenticated: false }, authLoading: false });
  },
  setSetupStage: (stage) => {
    try { localStorage.setItem('selena-setup-stage', stage); } catch {}
    set({ setupStage: stage });
  },
  setVoiceStatus: (voiceStatus: 'idle' | 'listening' | 'speaking') => set({ voiceStatus }),
  setSelectedLanguage: (lang) => {
    changeLanguage(lang);
    localStorage.setItem('selena-lang', lang);
    set({ selectedLanguage: lang });
    // Save language → rebuild prompts → THEN notify iframes
    fetch('/api/ui/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ language: lang }),
    })
      .then(() => fetch('/api/ui/setup/llm/rebuild', { method: 'POST' }))
      .then(() => {
        document.querySelectorAll('iframe').forEach(f => {
          try { f.contentWindow?.postMessage({ type: 'lang_changed' }, '*'); } catch (_) { }
        });
      })
      .catch(() => {
        // Notify iframes even on error so UI language at least updates
        document.querySelectorAll('iframe').forEach(f => {
          try { f.contentWindow?.postMessage({ type: 'lang_changed' }, '*'); } catch (_) { }
        });
      });
  },
  setTheme: (mode) => {
    localStorage.setItem('selena-theme', mode);
    applyThemeClass(mode);
    set({ theme: mode });
    broadcastThemeToIframes();
    // Broadcast to other browsers/devices
    fetch('/api/ui/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ theme: mode }),
    }).catch(() => { });
  },
  initThemeListener: () => {
    const theme = get().theme;
    applyThemeClass(theme);
    // Periodic re-check for time-of-day auto theme (every 60s)
    const interval = setInterval(() => {
      if (get().theme === 'auto') {
        applyThemeClass('auto');
        broadcastThemeToIframes();
      }
    }, 60_000);
    return () => clearInterval(interval);
  },

  fetchWizardStatus: async () => {
    // Only show the fullscreen loading spinner on the FIRST fetch (when
    // wizardLoading is still the initial `true`). Subsequent background
    // refreshes must NOT flip wizardLoading → true, otherwise the app
    // flashes the spinner every poll tick and unmounts the Wizard
    // component, wiping its local state mid-configuration.
    const isInitial = get().wizardLoading === true && !get().isConfigured && get().setupStage === 'landing';
    const maxRetries = 5;
    for (let attempt = 1; attempt <= maxRetries; attempt++) {
      try {
        const data = await apiFetch('/api/ui/wizard/status');
        set({ isConfigured: data?.completed === true, wizardLoading: false });
        return;
      } catch (e) {
        console.error(`fetchWizardStatus attempt ${attempt}/${maxRetries} failed`, e);
        if (attempt < maxRetries) {
          await new Promise(r => setTimeout(r, 2000));
        }
      }
    }
    // Only clear wizardLoading if we set it; otherwise leave it alone.
    if (isInitial) set({ isConfigured: false, wizardLoading: false });
    else set({ isConfigured: false });
  },

  fetchWizardRequirements: async () => {
    try {
      const data: WizardRequirements = await apiFetch('/api/ui/wizard/requirements');
      set({ wizardRequirements: data });
    } catch (e) {
      console.error('fetchWizardRequirements failed', e);
    }
  },


  fetchStats: async () => {
    try {
      const data = await apiFetch('/api/ui/system');
      if (data) {
        const hw = data.hardware ?? {};
        const core = data.core ?? {};
        const ollama = data.ollama ?? {};
        const llm = data.llm_engine ?? {};
        const nativeRaw: Array<Record<string, unknown>> = Array.isArray(data.native_services)
          ? data.native_services
          : [];
        const cloudRaw: Array<Record<string, unknown>> = Array.isArray(llm.cloud_providers)
          ? llm.cloud_providers
          : [];
        set({
          health: core ?? get().health,
          stats: {
            cpuTemp: hw.cpu_temp ?? 0,
            cpuLoad: hw.cpu_load ?? [0, 0, 0],
            cpuCount: hw.cpu_count ?? 1,
            ramUsedMb: hw.ram_used_mb ?? 0,
            ramTotalMb: hw.ram_total_mb ?? 1,
            swapUsedMb: hw.swap_used_mb ?? 0,
            swapTotalMb: hw.swap_total_mb ?? 0,
            diskUsedGb: hw.disk_used_gb ?? 0,
            diskTotalGb: hw.disk_total_gb ?? 1,
            uptime: core.uptime ?? 0,
            integrity: core.integrity ?? 'ok',
            mode: core.mode ?? 'normal',
            version: core.version ?? '—',
            corePort: core.core_port ?? 80,
            ollama: {
              installed: ollama.installed ?? false,
              running: ollama.running ?? false,
              model: ollama.model ?? null,
              modelLoaded: ollama.model_loaded ?? false,
              loadedModel: ollama.loaded_model,
              models: ollama.models,
            },
            llmEngine: {
              provider: (llm.provider as string) ?? 'ollama',
              model: (llm.model as string) ?? '',
              twoStep: Boolean(llm.two_step),
              cloudProviders: cloudRaw.map((p) => ({
                id: String(p.id ?? ''),
                name: String(p.name ?? p.id ?? ''),
                configured: Boolean(p.configured),
                model: String(p.model ?? ''),
                active: Boolean(p.active),
              })),
              intentCache: {
                size: Number((llm.intent_cache as { size?: number } | undefined)?.size ?? 0),
                hot: Number((llm.intent_cache as { hot?: number } | undefined)?.hot ?? 0),
              },
            },
            nativeServices: nativeRaw.map((s) => ({
              name: String(s.name ?? ''),
              running: Boolean(s.running),
              url: (s.url as string | null) ?? null,
              extra: (s.extra as Record<string, unknown>) ?? {},
            })),
          },
        });
      }
    } catch (e) {
      console.error('fetchStats failed', e);
    }
  },

  fetchDevices: async () => {
    set({ devicesLoading: true });
    try {
      const data = await apiFetch('/api/ui/devices');
      set({ devices: data?.devices ?? [] });
    } catch (e) {
      console.error('fetchDevices failed', e);
    } finally {
      set({ devicesLoading: false });
    }
  },

  fetchModules: async () => {
    set({ modulesLoading: true });
    try {
      const data = await apiFetch('/api/ui/modules');
      const mods = data?.modules ?? [];
      set({ modules: mods });
      get().initWidgetLayout(mods);
    } catch (e) {
      console.error('fetchModules failed', e);
    } finally {
      set({ modulesLoading: false });
    }
  },

  stopModule: async (name) => {
    await apiFetch(`/api/ui/modules/${name}/stop`, { method: 'POST' });
    // No fetchModules() — WebSocket module.stopped event updates state inline
  },

  startModule: async (name) => {
    await apiFetch(`/api/ui/modules/${name}/start`, { method: 'POST' });
    // No fetchModules() — WebSocket module.started event updates state inline
  },

  removeModule: async (name) => {
    await apiFetch(`/api/ui/modules/${name}`, { method: 'DELETE' });
    // No fetchModules() — WebSocket module.removed event updates state inline
  },

  updateDeviceState: async (deviceId, state) => {
    await apiFetch(`/api/ui/devices/${deviceId}/state`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ state }),
    });
    // No fetchDevices() — WebSocket device.state_changed event updates state inline
  },

  widgetLayout: loadWidgetLayout(),
  initWidgetLayout: (modules) => {
    set(state => {
      const layout = {
        pinned: [...state.widgetLayout.pinned],
        sizes: { ...state.widgetLayout.sizes },
        hidden: [...(state.widgetLayout.hidden ?? [])],
        screens: state.widgetLayout.screens ?? 1,
        positions: { ...(state.widgetLayout.positions ?? {}) },
        spans: { ...(state.widgetLayout.spans ?? {}) },
      };
      let changed = false;
      const occupiedSlots = new Set(Object.values(layout.positions));
      modules.forEach(m => {
        const hasWidget = !!m.ui?.widget?.file;
        if (!hasWidget) return;
        if (!(m.name in layout.sizes)) { layout.sizes[m.name] = 'normal'; changed = true; }
        // Only auto-pin non-SYSTEM modules that are not explicitly hidden
        if (m.type !== 'SYSTEM' && !layout.pinned.includes(m.name) && !layout.hidden.includes(m.name)) {
          layout.pinned.push(m.name);
          let slot = 0;
          while (occupiedSlots.has(slot)) slot++;
          layout.positions[m.name] = slot;
          occupiedSlots.add(slot);
          changed = true;
        }
      });
      // Assign positions for already-pinned modules that don't have one yet
      layout.pinned.forEach((name, idx) => {
        if (!(name in layout.positions)) {
          let slot = idx;
          while (occupiedSlots.has(slot)) slot++;
          layout.positions[name] = slot;
          occupiedSlots.add(slot);
          changed = true;
        }
      });
      if (changed) saveWidgetLayout(layout);
      return changed ? { widgetLayout: { ...layout } } : state;
    });
  },
  pinModule: (name, preferredSlot) => {
    set(state => {
      if (state.widgetLayout.pinned.includes(name)) return state;
      const hidden = (state.widgetLayout.hidden ?? []).filter(n => n !== name);
      const occupiedSlots = new Set(Object.values(state.widgetLayout.positions ?? {}));
      let slot: number;
      if (preferredSlot !== undefined && !occupiedSlots.has(preferredSlot)) {
        slot = preferredSlot;
      } else {
        slot = 0;
        while (occupiedSlots.has(slot)) slot++;
      }
      const layout: WidgetLayout = {
        pinned: [...state.widgetLayout.pinned, name],
        sizes: { ...state.widgetLayout.sizes, [name]: state.widgetLayout.sizes[name] ?? 'normal' },
        hidden,
        screens: state.widgetLayout.screens ?? 1,
        positions: { ...(state.widgetLayout.positions ?? {}), [name]: slot },
        spans: { ...(state.widgetLayout.spans ?? {}) },
      };
      saveWidgetLayout(layout);
      return { widgetLayout: layout };
    });
  },
  unpinModule: (name) => {
    set(state => {
      const hidden = [...(state.widgetLayout.hidden ?? [])];
      if (!hidden.includes(name)) hidden.push(name);
      const positions = { ...(state.widgetLayout.positions ?? {}) };
      delete positions[name];
      const spans = { ...(state.widgetLayout.spans ?? {}) };
      delete spans[name];
      const layout: WidgetLayout = { ...state.widgetLayout, pinned: state.widgetLayout.pinned.filter(n => n !== name), hidden, positions, spans };
      saveWidgetLayout(layout);
      return { widgetLayout: layout };
    });
  },
  setWidgetSize: (name, size) => {
    set(state => {
      const layout: WidgetLayout = { ...state.widgetLayout, sizes: { ...state.widgetLayout.sizes, [name]: size } };
      saveWidgetLayout(layout);
      return { widgetLayout: layout };
    });
  },
  swapWidgets: (nameA, nameB) => {
    set(state => {
      const positions = state.widgetLayout.positions ?? {};
      const posA = positions[nameA];
      const posB = positions[nameB];
      if (posA === undefined || posB === undefined) return state;
      const layout: WidgetLayout = {
        ...state.widgetLayout,
        positions: { ...positions, [nameA]: posB, [nameB]: posA },
      };
      saveWidgetLayout(layout);
      return { widgetLayout: layout };
    });
  },
  moveWidgetToSlot: (name, slotIdx) => {
    set(state => {
      const layout: WidgetLayout = {
        ...state.widgetLayout,
        positions: { ...(state.widgetLayout.positions ?? {}), [name]: slotIdx },
      };
      saveWidgetLayout(layout);
      return { widgetLayout: layout };
    });
  },
  setWidgetSpan: (name, cols, rows) => {
    set(state => {
      const layout: WidgetLayout = {
        ...state.widgetLayout,
        spans: { ...(state.widgetLayout.spans ?? {}), [name]: { cols, rows } },
      };
      saveWidgetLayout(layout);
      return { widgetLayout: layout };
    });
  },
  addScreen: () => {
    set(state => {
      const layout: WidgetLayout = { ...state.widgetLayout, screens: (state.widgetLayout.screens ?? 1) + 1 };
      saveWidgetLayout(layout);
      return { widgetLayout: layout };
    });
  },

  connectSyncStream: () => {
    let lastVersion = 0;
    let ws: WebSocket | null = null;
    let backoff = 2000;
    let destroyed = false;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    // Parse layout from raw object
    function parseLayout(raw: Record<string, unknown>): WidgetLayout {
      return {
        pinned: Array.isArray(raw.pinned) ? raw.pinned as string[] : [],
        sizes: (raw.sizes ?? {}) as Record<string, 'compact' | 'normal'>,
        hidden: Array.isArray(raw.hidden) ? raw.hidden as string[] : [],
        screens: typeof raw.screens === 'number' ? raw.screens : 1,
        positions: (raw.positions && typeof raw.positions === 'object' && !Array.isArray(raw.positions))
          ? raw.positions as Record<string, number> : {},
        spans: (raw.spans && typeof raw.spans === 'object' && !Array.isArray(raw.spans))
          ? raw.spans as Record<string, { cols: number; rows: number }> : {},
      };
    }

    // Apply full authoritative state from server (overwrite localStorage)
    function applyFullState(msg: Record<string, unknown>) {
      const settings = (msg.settings ?? {}) as Record<string, string>;
      const layout = (msg.layout ?? {}) as Record<string, unknown>;

      // Settings: theme
      if (settings.theme && settings.theme !== get().theme) {
        localStorage.setItem('selena-theme', settings.theme);
        const mode = settings.theme as ThemeMode;
        applyThemeClass(mode);
        set({ theme: mode });
        broadcastThemeToIframes();
      }
      // Settings: language
      if (settings.language && settings.language !== get().selectedLanguage) {
        localStorage.setItem('selena-lang', settings.language);
        changeLanguage(settings.language);
        set({ selectedLanguage: settings.language });
        document.querySelectorAll('iframe').forEach(f => {
          try { f.contentWindow?.postMessage({ type: 'lang_changed' }, '*'); } catch (_) { /* cross-origin */ }
        });
      }
      // Layout
      if (layout && (Array.isArray(layout.pinned) || layout.hidden !== undefined)) {
        const parsed = parseLayout(layout);
        try { localStorage.setItem('selena-widget-layout', JSON.stringify(parsed)); } catch { /* ignore */ }
        set({ widgetLayout: parsed });
      }

      // Devices snapshot (from enriched hello)
      if (Array.isArray(msg.devices)) {
        set({ devices: msg.devices as Device[] });
      }

      // Modules snapshot
      if (Array.isArray(msg.modules)) {
        set({ modules: msg.modules as Module[] });
      }

      // System metrics snapshot (HW only — no ollama/llm probes)
      const sys = msg.system as Record<string, unknown> | undefined;
      if (sys) {
        set(state => {
          const base = state.stats ?? {} as SystemStats;
          return {
            stats: {
              ...base,
              cpuTemp: (sys.cpu_temp as number) ?? base.cpuTemp ?? 0,
              cpuLoad: (sys.cpu_load as [number, number, number]) ?? base.cpuLoad ?? [0, 0, 0],
              cpuCount: (sys.cpu_count as number) ?? base.cpuCount ?? 1,
              ramUsedMb: (sys.ram_used_mb as number) ?? base.ramUsedMb ?? 0,
              ramTotalMb: (sys.ram_total_mb as number) ?? base.ramTotalMb ?? 0,
              swapUsedMb: (sys.swap_used_mb as number) ?? base.swapUsedMb ?? 0,
              swapTotalMb: (sys.swap_total_mb as number) ?? base.swapTotalMb ?? 0,
              diskUsedGb: (sys.disk_used_gb as number) ?? base.diskUsedGb ?? 0,
              diskTotalGb: (sys.disk_total_gb as number) ?? base.diskTotalGb ?? 0,
              uptime: (sys.uptime as number) ?? base.uptime ?? 0,
              integrity: base.integrity ?? 'ok',
              mode: (sys.mode as string) ?? base.mode ?? 'normal',
              version: (sys.version as string) ?? base.version ?? '',
              corePort: base.corePort ?? 7070,
              ollama: base.ollama ?? { installed: false, running: false, model: null, modelLoaded: false },
              llmEngine: base.llmEngine ?? { provider: '', model: '', twoStep: false, cloudProviders: [], intentCache: { size: 0, hot: 0 } },
              nativeServices: base.nativeServices ?? [],
            } as SystemStats,
          };
        });
      }

      // Voice state snapshot
      const voice = msg.voice as Record<string, unknown> | undefined;
      if (voice) {
        const st = voice.state as string;
        if (st === 'speaking') set({ voiceStatus: 'speaking' });
        else if (st === 'listening') set({ voiceStatus: 'listening' });
        else set({ voiceStatus: 'idle' });
      }
    }

    // Apply a single delta event
    function applyEvent(msg: { event_type: string; payload?: unknown }) {
      const eventType = msg.event_type;
      const payload = msg.payload as Record<string, unknown> | undefined;
      if (!payload) return;

      // ── Layout ──
      if (eventType === 'layout_changed') {
        const layout = parseLayout(payload);
        try { localStorage.setItem('selena-widget-layout', JSON.stringify(layout)); } catch { /* ignore */ }
        set({ widgetLayout: layout });

      // ── Settings ──
      } else if (eventType === 'settings_changed') {
        const p = payload as Record<string, string>;
        if (p.theme && p.theme !== get().theme) {
          localStorage.setItem('selena-theme', p.theme);
          const mode = p.theme as ThemeMode;
          applyThemeClass(mode);
          set({ theme: mode });
          broadcastThemeToIframes();
        }
        if (p.language && p.language !== get().selectedLanguage) {
          localStorage.setItem('selena-lang', p.language);
          changeLanguage(p.language);
          set({ selectedLanguage: p.language });
          document.querySelectorAll('iframe').forEach(f => {
            try { f.contentWindow?.postMessage({ type: 'lang_changed' }, '*'); } catch (_) { /* cross-origin */ }
          });
        }

      // ── Theme/wallpaper events ──
      } else if (eventType === 'theme_vars_changed' || eventType === 'themes_changed') {
        get().fetchThemes();

      // ── Device events ──
      } else if (eventType === 'device.state_changed') {
        const deviceId = payload.device_id as string | undefined;
        if (deviceId) {
          set(state => ({
            devices: state.devices.map(d =>
              d.device_id === deviceId
                ? { ...d, state: { ...d.state, ...(payload.new_state as Record<string, unknown> ?? {}) }, last_seen: (payload.timestamp as number) ?? d.last_seen }
                : d
            ),
          }));
        }
      } else if (eventType === 'device.registered') {
        get().fetchDevices();
      } else if (eventType === 'device.removed') {
        const deviceId = payload.device_id as string | undefined;
        if (deviceId) {
          set(state => ({ devices: state.devices.filter(d => d.device_id !== deviceId) }));
        }
      } else if (eventType === 'device.offline' || eventType === 'device.online') {
        const deviceId = payload.device_id as string | undefined;
        if (deviceId) {
          set(state => ({
            devices: state.devices.map(d =>
              d.device_id === deviceId
                ? { ...d, meta: { ...d.meta, watchdog_online: eventType === 'device.online' } }
                : d
            ),
          }));
        }

      // ── Module events (inline update, no REST call) ──
      } else if (eventType === 'module.started' || eventType === 'module.stopped') {
        const name = payload.name as string | undefined;
        if (name) {
          set(state => ({
            modules: state.modules.map(m =>
              m.name === name
                ? { ...m, status: eventType === 'module.started' ? 'RUNNING' : 'STOPPED' }
                : m
            ),
          }));
        }
      } else if (eventType === 'module.removed') {
        const name = payload.name as string | undefined;
        if (name) {
          set(state => ({ modules: state.modules.filter(m => m.name !== name) }));
        }
      } else if (eventType === 'module.installed') {
        get().fetchModules();

      // ── Voice events ──
      } else if (eventType === 'voice.state') {
        const st = payload.state as string | undefined;
        if (st === 'speaking') set({ voiceStatus: 'speaking' });
        else if (st === 'listening') set({ voiceStatus: 'listening' });
        else set({ voiceStatus: 'idle' });
      } else if (eventType === 'voice.privacy_on') {
        set({ voiceStatus: 'idle' });
      } else if (eventType === 'voice.privacy_off') {
        // no-op, voice will resume when next wake word is detected

      // ── System metrics (HW only — merge into stats without overwriting ollama/llm) ──
      } else if (eventType === 'monitor.metrics') {
        set(state => {
          if (!state.stats) return {};
          return {
            stats: {
              ...state.stats,
              cpuTemp: (payload.cpu_temp_c as number) ?? state.stats.cpuTemp,
              cpuLoad: (payload.cpu_load as [number, number, number]) ?? state.stats.cpuLoad,
              ramUsedMb: (payload.ram_used_mb as number) ?? state.stats.ramUsedMb,
              ramTotalMb: (payload.ram_total_mb as number) ?? state.stats.ramTotalMb,
              swapUsedMb: (payload.swap_used_mb as number) ?? state.stats.swapUsedMb,
              swapTotalMb: (payload.swap_total_mb as number) ?? state.stats.swapTotalMb,
              diskUsedGb: (payload.disk_used_gb as number) ?? state.stats.diskUsedGb,
              diskTotalGb: (payload.disk_total_gb as number) ?? state.stats.diskTotalGb,
              uptime: (payload.uptime as number) ?? state.stats.uptime,
            },
          };
        });

      // ── Notification push ──
      } else if (eventType === 'notification.sent') {
        // Handled by components that listen to store; add to a notifications list if needed
      }

      // ── Relay all events to widget iframes via postMessage ──
      document.querySelectorAll('iframe').forEach(f => {
        try {
          f.contentWindow?.postMessage({
            type: 'selena_event',
            event_type: eventType,
            payload,
          }, '*');
        } catch (_) { /* cross-origin */ }
      });
    }

    function connect() {
      if (destroyed) return;
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
      ws = new WebSocket(`${proto}//${location.host}/api/ui/sync?v=${lastVersion}`);

      ws.onopen = () => {
        backoff = 2000; // reset backoff on successful connect
      };

      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          set({ lastServerContact: Date.now() });

          if (msg.type === 'hello') {
            lastVersion = msg.version ?? 0;
            applyFullState(msg);
          } else if (msg.type === 'replay') {
            const events = msg.events as Array<{ version: number; event_type: string; payload?: unknown }>;
            for (const e of events) {
              applyEvent(e);
              lastVersion = Math.max(lastVersion, e.version ?? 0);
            }
          } else if (msg.type === 'event') {
            lastVersion = msg.version ?? lastVersion;
            applyEvent(msg);
          } else if (msg.type === 'ping') {
            lastVersion = msg.version ?? lastVersion;
            ws?.send('{"type":"pong"}');
          }
        } catch { /* ignore malformed messages */ }
      };

      ws.onclose = () => {
        ws = null;
        if (!destroyed) {
          reconnectTimer = setTimeout(() => {
            connect();
          }, backoff);
          backoff = Math.min(backoff * 1.5, 30000);
        }
      };

      ws.onerror = () => {
        // onclose will fire after onerror — reconnection happens there
      };
    }

    connect();

    return () => {
      destroyed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      ws?.close();
    };
  },

  lastServerContact: Date.now(),

  toast: null,
  showToast: (message, type = 'success') => {
    set({ toast: { message, type } });
    setTimeout(() => set({ toast: null }), 2500);
  },

  // ── Custom Themes ──────────────────────────────────────────────────────────
  customThemes: [],
  activeThemeId: 'default',
  fetchThemes: async () => {
    try {
      const data = await apiFetch('/api/ui/themes') as {
        active: string;
        wallpaper: string | null;
        wallpaper_blur: number;
        wallpaper_opacity: number;
        themes: CustomTheme[];
      };
      set({
        customThemes: data.themes ?? [],
        activeThemeId: data.active ?? 'default',
        activeWallpaper: data.wallpaper ?? null,
        wallpaperBlur: data.wallpaper_blur ?? 0,
        wallpaperOpacity: data.wallpaper_opacity ?? 0.15,
      });
      const active = (data.themes ?? []).find(t => t.id === data.active) ?? null;
      applyCustomThemeCSS(active);
      applyWallpaperClass(!!data.wallpaper);
      broadcastThemeVarsToIframes();
    } catch (e) {
      console.error('fetchThemes failed', e);
    }
  },
  activateTheme: async (id) => {
    try {
      await fetch('/api/ui/themes/active', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id }),
      });
      set({ activeThemeId: id });
      const theme = get().customThemes.find(t => t.id === id) ?? null;
      applyCustomThemeCSS(theme);
      broadcastThemeVarsToIframes();
    } catch (e) {
      console.error('activateTheme failed', e);
    }
  },
  createTheme: async (name, dark, light) => {
    try {
      const res = await fetch('/api/ui/themes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, dark, light }),
      });
      if (!res.ok) return null;
      const theme = await res.json() as CustomTheme;
      set(s => ({ customThemes: [...s.customThemes, theme] }));
      return theme;
    } catch (e) {
      console.error('createTheme failed', e);
      return null;
    }
  },
  updateTheme: async (id, name, dark, light) => {
    try {
      const res = await fetch(`/api/ui/themes/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, dark, light }),
      });
      if (!res.ok) return;
      const updated = await res.json() as CustomTheme;
      set(s => ({ customThemes: s.customThemes.map(t => t.id === id ? updated : t) }));
      if (get().activeThemeId === id) applyCustomThemeCSS(updated);
    } catch (e) {
      console.error('updateTheme failed', e);
    }
  },
  deleteTheme: async (id) => {
    try {
      await fetch(`/api/ui/themes/${id}`, { method: 'DELETE' });
      set(s => ({ customThemes: s.customThemes.filter(t => t.id !== id) }));
      if (get().activeThemeId === id) {
        set({ activeThemeId: 'default' });
        applyCustomThemeCSS(null);
        broadcastThemeVarsToIframes();
      }
    } catch (e) {
      console.error('deleteTheme failed', e);
    }
  },

  // ── Wallpapers ─────────────────────────────────────────────────────────────
  wallpapers: [],
  activeWallpaper: null,
  wallpaperBlur: 0,
  wallpaperOpacity: 0.15,
  fetchWallpapers: async () => {
    try {
      const data = await apiFetch('/api/ui/wallpapers') as WallpaperInfo[];
      set({ wallpapers: data ?? [] });
    } catch (e) {
      console.error('fetchWallpapers failed', e);
    }
  },
  setWallpaper: async (filename, blur = 0, opacity = 0.15) => {
    set({ activeWallpaper: filename, wallpaperBlur: blur, wallpaperOpacity: opacity });
    applyWallpaperClass(!!filename);
    broadcastThemeVarsToIframes();
    try {
      await fetch('/api/ui/wallpapers/active', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ wallpaper: filename, blur, opacity }),
      });
    } catch (e) {
      console.error('setWallpaper failed', e);
    }
  },
}));

