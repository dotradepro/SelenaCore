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

interface AppState {
  isConfigured: boolean;
  wizardLoading: boolean;
  setupStage: 'landing' | 'wizard';
  selectedLanguage: string;
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
}

async function apiFetch(path: string, opts?: RequestInit) {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  if (res.status === 204) return null;
  return res.json();
}

export const useStore = create<AppState>((set, get) => ({
  isConfigured: false,
  wizardLoading: true,
  setupStage: 'landing',
  selectedLanguage: localStorage.getItem('selena-lang') || 'en',
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

  fetchHealth: async () => {
    try {
      const data = await apiFetch('/api/ui/system');
      if (data?.core) {
        set({ health: data.core });
      }
    } catch (e) {
      console.error('fetchHealth failed', e);
    }
  },

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
            version: core.version ?? '0.3.0-beta',
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
      set({ modules: data?.modules ?? [] });
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
}));

