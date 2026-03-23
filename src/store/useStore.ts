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
    widget?: { file?: string; size?: string };
    settings?: string;
  };
}

export interface SystemStats {
  cpuTemp: number;
  ramUsedMb: number;
  ramTotalMb: number;
  diskUsedGb: number;
  diskTotalGb: number;
  uptime: number;
  integrity: string;
  mode: string;
  version: string;
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

export type ThemeMode = 'auto' | 'light' | 'dark';

function loadTheme(): ThemeMode {
  const stored = localStorage.getItem('selena-theme');
  if (stored === 'light' || stored === 'dark' || stored === 'auto') return stored;
  return 'auto';
}

function applyThemeClass(mode: ThemeMode) {
  const root = document.documentElement;
  if (mode === 'auto') {
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    root.classList.toggle('light', !prefersDark);
  } else {
    root.classList.toggle('light', mode === 'light');
  }
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
  user: { name: string; role: string } | null;
  health: Health | null;
  stats: SystemStats | null;
  devices: Device[];
  modules: Module[];
  devicesLoading: boolean;
  modulesLoading: boolean;
  setConfigured: (status: boolean) => void;
  setUser: (user: { name: string; role: string }) => void;
  setSetupStage: (stage: 'landing' | 'wizard') => void;
  setSelectedLanguage: (lang: string) => void;
  fetchWizardStatus: () => Promise<void>;
  fetchWizardRequirements: () => Promise<void>;
  fetchHealth: () => Promise<void>;
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
  moveWidget: (name: string, dir: -1 | 1) => void;
  swapWidgets: (nameA: string, nameB: string) => void;
  moveWidgetToSlot: (name: string, slot: number) => void;
  setWidgetSpan: (name: string, cols: number, rows: number) => void;
  addScreen: () => void;
  connectSyncStream: () => () => void;
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

let _lastModulesFetch = 0;
let _modulesFetchTimer: ReturnType<typeof setTimeout> | null = null;

function debounceFetchModules(getFn: () => AppState) {
  if (_modulesFetchTimer) clearTimeout(_modulesFetchTimer);
  // Skip if a direct fetch (from mutation) ran within the last 2 s
  if (Date.now() - _lastModulesFetch < 2_000) return;
  _modulesFetchTimer = setTimeout(() => { getFn().fetchModules(); }, 400);
}

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
  setupStage: 'landing',
  selectedLanguage: localStorage.getItem('selena-lang') || 'en',
  theme: loadTheme(),
  wizardRequirements: null,
  user: null,
  health: null,
  stats: null,
  devices: [],
  modules: [],
  devicesLoading: false,
  modulesLoading: false,
  voiceStatus: 'idle' as 'idle' | 'listening' | 'speaking',

  setConfigured: (status) => set({ isConfigured: status }),
  setUser: (user) => set({ user }),
  setSetupStage: (stage) => set({ setupStage: stage }),
  setVoiceStatus: (voiceStatus: 'idle' | 'listening' | 'speaking') => set({ voiceStatus }),
  setSelectedLanguage: (lang) => {
    changeLanguage(lang);
    set({ selectedLanguage: lang });
  },
  setTheme: (mode) => {
    localStorage.setItem('selena-theme', mode);
    applyThemeClass(mode);
    set({ theme: mode });
  },
  initThemeListener: () => {
    const theme = get().theme;
    applyThemeClass(theme);
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const handler = () => { if (get().theme === 'auto') applyThemeClass('auto'); };
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  },

  fetchWizardStatus: async () => {
    set({ wizardLoading: true });
    try {
      const data = await apiFetch('/api/ui/wizard/status');
      set({ isConfigured: data?.completed === true });
    } catch (e) {
      console.error('fetchWizardStatus failed', e);
      set({ isConfigured: false });
    } finally {
      set({ wizardLoading: false });
    }
  },

  fetchWizardRequirements: async () => {
    try {
      const data: WizardRequirements = await apiFetch('/api/ui/wizard/requirements');
      set({ wizardRequirements: data });
    } catch (e) {
      console.error('fetchWizardRequirements failed', e);
    }
  },

  // fetchHealth is an alias for fetchStats — both use /api/ui/system
  fetchHealth: async () => { await get().fetchStats(); },

  fetchStats: async () => {
    try {
      const data = await apiFetch('/api/ui/system');
      if (data) {
        const hw = data.hardware ?? {};
        const core = data.core ?? {};
        set({
          health: core ?? get().health,
          stats: {
            cpuTemp: hw.cpu_temp ?? 0,
            ramUsedMb: hw.ram_used_mb ?? 0,
            ramTotalMb: hw.ram_total_mb ?? 1,
            diskUsedGb: hw.disk_used_gb ?? 0,
            diskTotalGb: hw.disk_total_gb ?? 1,
            uptime: core.uptime ?? 0,
            integrity: core.integrity ?? 'ok',
            mode: core.mode ?? 'normal',
            version: core.version ?? '—',
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
    _lastModulesFetch = Date.now();
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
    get().fetchModules();
  },

  startModule: async (name) => {
    await apiFetch(`/api/ui/modules/${name}/start`, { method: 'POST' });
    get().fetchModules();
  },

  removeModule: async (name) => {
    await apiFetch(`/api/ui/modules/${name}`, { method: 'DELETE' });
    get().fetchModules();
  },

  updateDeviceState: async (deviceId, state) => {
    await apiFetch(`/api/ui/devices/${deviceId}/state`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ state }),
    });
    get().fetchDevices();
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
  moveWidget: (name, dir) => {
    // legacy: kept for compatibility
    set(state => {
      const pinned = [...state.widgetLayout.pinned];
      const idx = pinned.indexOf(name);
      if (idx < 0) return state;
      const newIdx = idx + dir;
      if (newIdx < 0 || newIdx >= pinned.length) return state;
      [pinned[idx], pinned[newIdx]] = [pinned[newIdx], pinned[idx]];
      const layout: WidgetLayout = { ...state.widgetLayout, pinned };
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
    // Fetch layout from backend first (authoritative, shared across devices)
    fetch('/api/ui/layout')
      .then(r => r.json())
      .then((raw: Record<string, unknown>) => {
        if (raw && Array.isArray(raw.pinned)) {
          const layout: WidgetLayout = {
            pinned: raw.pinned as string[],
            sizes: (raw.sizes ?? {}) as Record<string, 'compact' | 'normal'>,
            hidden: Array.isArray(raw.hidden) ? raw.hidden as string[] : [],
            screens: typeof raw.screens === 'number' ? raw.screens : 1,
            positions: (raw.positions && typeof raw.positions === 'object' && !Array.isArray(raw.positions))
              ? raw.positions as Record<string, number> : {},
            spans: (raw.spans && typeof raw.spans === 'object' && !Array.isArray(raw.spans))
              ? raw.spans as Record<string, { cols: number; rows: number }> : {},
          };
          try { localStorage.setItem('selena-widget-layout', JSON.stringify(layout)); } catch { /* ignore */ }
          set({ widgetLayout: layout });
        }
      })
      .catch(() => { /* ignore */ });

    // Open SSE stream for real-time sync
    const es = new EventSource('/api/ui/stream');
    es.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data) as { type: string; payload?: unknown };
        if (msg.type === 'layout_changed' && msg.payload) {
          const raw = msg.payload as Record<string, unknown>;
          const layout: WidgetLayout = {
            pinned: Array.isArray(raw.pinned) ? raw.pinned as string[] : [],
            sizes: (raw.sizes ?? {}) as Record<string, 'compact' | 'normal'>,
            hidden: Array.isArray(raw.hidden) ? raw.hidden as string[] : [],
            screens: typeof raw.screens === 'number' ? raw.screens : 1,
            positions: (raw.positions && typeof raw.positions === 'object' && !Array.isArray(raw.positions))
              ? raw.positions as Record<string, number> : {},
            spans: (raw.spans && typeof raw.spans === 'object' && !Array.isArray(raw.spans))
              ? raw.spans as Record<string, { cols: number; rows: number }> : {},
          };
          try { localStorage.setItem('selena-widget-layout', JSON.stringify(layout)); } catch { /* ignore */ }
          set({ widgetLayout: layout });
        } else if (
          msg.type === 'module.started' ||
          msg.type === 'module.stopped' ||
          msg.type === 'module.removed'
        ) {
          debounceFetchModules(get);
        }
      } catch { /* ignore */ }
    };
    es.onerror = () => {
      // Reconnect handled automatically by EventSource; nothing to do here.
    };
    return () => es.close();
  },
}));

