import { useEffect, useState } from 'react';
import { useStore } from '../store/useStore';

/**
 * Matches the RolePermissions dataclass returned by the backend.
 * All fields optional so callers can safely use `perms?.devices_control`.
 */
export interface RolePermissions {
  devices_view: boolean;
  devices_control: boolean;
  scenes_run: 'all' | 'approved' | 'none';
  modules_configure: boolean;
  users_manage: boolean;
  roles_configure: boolean;
  system_reboot: boolean;
  system_update: boolean;
  integrity_logs_view: boolean;
  voice_commands: 'all' | 'basic' | 'none';
  allowed_device_types: string[];
  allowed_widget_ids: string[];
}

const UM_BASE = '/api/ui/modules/user-manager';
const CACHE: Partial<Record<string, RolePermissions>> = {};

/**
 * Returns the permissions for the current user's role.
 * Cached per role per page load to avoid repeated fetches.
 */
export function usePermissions(): { perms: RolePermissions | null; loading: boolean } {
  const user = useStore((s) => s.user);
  const role = user?.role ?? null;

  const [perms, setPerms] = useState<RolePermissions | null>(null);
  const [loading, setLoading] = useState(false);

  // Owner always has all permissions — no network call needed
  if (role === 'owner' && !perms) {
    const ownerPerms: RolePermissions = {
      devices_view: true,
      devices_control: true,
      scenes_run: 'all',
      modules_configure: true,
      users_manage: true,
      roles_configure: true,
      system_reboot: true,
      system_update: true,
      integrity_logs_view: true,
      voice_commands: 'all',
      allowed_device_types: [],
      allowed_widget_ids: [],
    };
    return { perms: ownerPerms, loading: false };
  }

  useEffect(() => {
    if (!role) return;
    if (CACHE[role]) {
      setPerms(CACHE[role]!);
      return;
    }
    setLoading(true);
    const token = localStorage.getItem('selena_device') ?? undefined;
    fetch(`${UM_BASE}/roles/${encodeURIComponent(role)}/permissions`, {
      headers: token ? { 'X-Device-Token': token } : {},
      credentials: 'include',
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data?.permissions) {
          CACHE[role] = data.permissions as RolePermissions;
          setPerms(data.permissions as RolePermissions);
        }
      })
      .catch(() => { /* keep perms null */ })
      .finally(() => setLoading(false));
  }, [role]);

  return { perms, loading };
}
