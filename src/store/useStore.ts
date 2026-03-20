import { create } from 'zustand';

export interface Module {
  id: string;
  name: string;
  type: string;
  status: string;
  version: string;
  size: string;
}

interface AppState {
  isConfigured: boolean;
  user: { name: string; role: string } | null;
  systemStats: { cpuTemp: number; ramFree: number; diskFree: number };
  modules: Module[];
  setConfigured: (status: boolean) => void;
  setUser: (user: { name: string; role: string }) => void;
  fetchStats: () => Promise<void>;
  fetchModules: () => Promise<void>;
  toggleModule: (id: string) => Promise<void>;
}

export const useStore = create<AppState>((set, get) => ({
  isConfigured: false,
  user: null,
  systemStats: { cpuTemp: 45, ramFree: 420, diskFree: 12000 },
  modules: [],
  setConfigured: (status) => set({ isConfigured: status }),
  setUser: (user) => set({ user }),
  fetchStats: async () => {
    try {
      const res = await fetch('/api/system/stats');
      if (res.ok) {
        const stats = await res.json();
        set({ systemStats: stats });
      }
    } catch (e) {
      console.error('Failed to fetch stats', e);
    }
  },
  fetchModules: async () => {
    try {
      const res = await fetch('/api/modules');
      if (res.ok) {
        const modules = await res.json();
        set({ modules });
      }
    } catch (e) {
      console.error('Failed to fetch modules', e);
    }
  },
  toggleModule: async (id: string) => {
    try {
      const res = await fetch(`/api/modules/${id}/toggle`, { method: 'POST' });
      if (res.ok) {
        get().fetchModules(); // Refresh list
      }
    } catch (e) {
      console.error('Failed to toggle module', e);
    }
  }
}));
