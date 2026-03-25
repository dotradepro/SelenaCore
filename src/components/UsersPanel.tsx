/**
 * UsersPanel — full user management UI for Settings → Users tab.
 * Talks to /api/ui/modules/user-manager/ endpoints.
 * Auth: X-Device-Token header + X-Elevated-Token for sensitive ops.
 */
import { useState, useEffect, useCallback, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Users, Plus, Trash2, Edit3, Key, Smartphone, QrCode,
  ChevronDown, ChevronUp, Shield, Check, X, AlertCircle,
  Eye, EyeOff, RefreshCw,
} from 'lucide-react';
import { cn } from '../lib/utils';
import { useStore } from '../store/useStore';
import { useElevated } from '../hooks/useElevated';
import PinConfirmModal from './PinConfirmModal';

const UM = '/api/ui/modules/user-manager';

function getToken() {
  return localStorage.getItem('selena_device') ?? undefined;
}
function getElev() {
  return sessionStorage.getItem('selena_elevated') ?? undefined;
}

function authHeaders(needElev = false): HeadersInit {
  const h: Record<string, string> = { 'Content-Type': 'application/json' };
  const t = getToken();
  if (t) h['X-Device-Token'] = t;
  if (needElev) {
    const e = getElev();
    if (e) h['X-Elevated-Token'] = e;
  }
  return h;
}

// ── role display helpers ──────────────────────────────────────────────────────
const ROLE_COLOR: Record<string, string> = {
  owner: 'bg-violet-500/20 text-violet-300 border-violet-500/30',
  admin: 'bg-blue-500/20 text-blue-300 border-blue-500/30',
  user: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30',
  guest: 'bg-zinc-500/20 text-zinc-400 border-zinc-500/30',
};

function RoleBadge({ role }: { role: string }) {
  return (
    <span className={cn(
      'text-[10px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded border',
      ROLE_COLOR[role] ?? ROLE_COLOR.guest,
    )}>
      {role}
    </span>
  );
}

// ── types ─────────────────────────────────────────────────────────────────────
interface UserProfile {
  user_id: string;
  username: string;
  role: string;
  created_at: number;
}

interface RegisteredDevice {
  device_id: string;
  device_name: string;
  ip: string | null;
  user_agent: string | null;
  created_at: number;
  last_seen: number | null;
  active: boolean;
}

interface RolePerms {
  devices_view: boolean;
  devices_control: boolean;
  scenes_run: string;
  modules_configure: boolean;
  users_manage: boolean;
  roles_configure: boolean;
  system_reboot: boolean;
  system_update: boolean;
  integrity_logs_view: boolean;
  voice_commands: string;
}

// ═════════════════════════════════════════════════════════════════════════════
export default function UsersPanel() {
  const { t } = useTranslation();
  const currentUser = useStore((s) => s.user);
  const { pinModalProps } = useElevated();

  const [activeTab, setActiveTab] = useState<'users' | 'roles'>('users');
  const isOwner = currentUser?.role === 'owner';
  const isAdmin = currentUser?.role === 'admin' || isOwner;

  return (
    <div className="space-y-6">
      <div>
        <h3 style={{ fontSize: 20, fontWeight: 600, marginBottom: 4, color: 'var(--tx)' }}>
          {t('usersPanel.title')}
        </h3>
        <p style={{ fontSize: 13, color: 'var(--tx2)' }}>{t('usersPanel.desc')}</p>
      </div>

      {/* Tab switcher */}
      {isOwner && (
        <div className="flex gap-1 p-1 bg-zinc-900 rounded-lg w-fit border border-zinc-800">
          {(['users', 'roles'] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={cn(
                'px-3 py-1.5 text-xs font-medium rounded-md transition-all',
                activeTab === tab
                  ? 'bg-zinc-700 text-zinc-50'
                  : 'text-zinc-500 hover:text-zinc-300',
              )}
            >
              {t(`usersPanel.tab_${tab}`)}
            </button>
          ))}
        </div>
      )}

      {activeTab === 'users' && <UsersList canManage={isAdmin} isOwner={isOwner} />}
      {activeTab === 'roles' && isOwner && <RolesEditor />}

      <PinConfirmModal {...pinModalProps} />
    </div>
  );
}

// ═════════════════════════════════════════════════════════════════════════════
//  Users list
// ═════════════════════════════════════════════════════════════════════════════
function UsersList({ canManage, isOwner }: { canManage: boolean; isOwner: boolean }) {
  const { t } = useTranslation();
  const [users, setUsers] = useState<UserProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [qrData, setQrData] = useState<{ image: string; join_url: string; expires_in: number } | null>(null);
  const [qrLoading, setQrLoading] = useState(false);
  const { requestElevation, pinModalProps } = useElevated();

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${UM}/users`, { headers: authHeaders(), credentials: 'include' });
      if (!res.ok) throw new Error();
      const d = await res.json();
      setUsers(d.users ?? []);
    } catch {
      setError(t('common.error'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => { load(); }, [load]);

  const deleteUser = (u: UserProfile) => {
    requestElevation(async () => {
      if (!confirm(t('usersPanel.confirmDelete', { name: u.username }))) return;
      await fetch(`${UM}/users/${u.user_id}`, {
        method: 'DELETE',
        headers: authHeaders(true),
        credentials: 'include',
      });
      load();
    });
  };

  const generateQr = async () => {
    setQrLoading(true);
    try {
      const res = await fetch(`${UM}/auth/qr/start`, {
        method: 'POST',
        headers: authHeaders(false),
        credentials: 'include',
      });
      if (!res.ok) throw new Error();
      const d = await res.json();
      setQrData({ image: d.qr_image, join_url: d.join_url, expires_in: d.expires_in });
    } catch { /* ignore */ } finally {
      setQrLoading(false);
    }
  };

  if (loading) return <Spinner />;
  if (error) return <ErrorMsg msg={error} onRetry={load} />;

  return (
    <div className="space-y-4">
      {/* Action bar */}
      <div className="flex items-center gap-2 justify-between">
        <span className="text-xs text-zinc-500">
          {t('usersPanel.userCount', { count: users.length })}
        </span>
        {canManage && (
          <div className="flex gap-2">
            <button
              onClick={generateQr}
              disabled={qrLoading}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-zinc-300 bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 rounded-lg transition-colors"
            >
              <QrCode size={13} />
              {t('usersPanel.qrInvite')}
            </button>
            <button
              onClick={() => setShowCreate(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-white bg-violet-600 hover:bg-violet-500 rounded-lg transition-colors"
            >
              <Plus size={13} />
              {t('usersPanel.addUser')}
            </button>
          </div>
        )}
      </div>

      {/* QR panel */}
      {qrData && (
        <div className="p-4 bg-zinc-900 border border-zinc-700 rounded-xl flex items-start gap-4">
          <div
            className="w-32 h-32 shrink-0 bg-white rounded-lg p-1"
            dangerouslySetInnerHTML={{ __html: qrData.image }}
          />
          <div className="space-y-1">
            <p className="text-sm font-medium">{t('usersPanel.qrTitle')}</p>
            <p className="text-xs text-zinc-500">{t('usersPanel.qrDesc')}</p>
            <a
              href={qrData.join_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-violet-400 hover:underline break-all"
            >
              {qrData.join_url}
            </a>
            <p className="text-[11px] text-zinc-600">
              {t('usersPanel.qrExpires', { sec: qrData.expires_in })}
            </p>
          </div>
          <button onClick={() => setQrData(null)} className="ml-auto text-zinc-600 hover:text-zinc-300">
            <X size={14} />
          </button>
        </div>
      )}

      {/* User rows */}
      {users.length === 0 ? (
        <p className="text-sm text-zinc-500 py-4">{t('usersPanel.noUsers')}</p>
      ) : (
        <div className="space-y-2">
          {users.map((u) => (
            <UserRow
              key={u.user_id}
              user={u}
              isOwner={isOwner}
              canManage={canManage}
              expanded={expandedId === u.user_id}
              onToggle={() => setExpandedId((id) => id === u.user_id ? null : u.user_id)}
              onDelete={() => deleteUser(u)}
              onRefresh={load}
              requestElevation={requestElevation}
            />
          ))}
        </div>
      )}

      {showCreate && (
        <CreateUserModal
          isOwner={isOwner}
          onClose={() => setShowCreate(false)}
          onCreated={() => { setShowCreate(false); load(); }}
          requestElevation={requestElevation}
        />
      )}

      <PinConfirmModal {...pinModalProps} />
    </div>
  );
}

// ─── Single user row ──────────────────────────────────────────────────────────
function UserRow({
  user, isOwner, canManage, expanded, onToggle, onDelete, onRefresh, requestElevation,
}: {
  user: UserProfile;
  isOwner: boolean;
  canManage: boolean;
  expanded: boolean;
  onToggle: () => void;
  onDelete: () => void;
  onRefresh: () => void;
  requestElevation: (fn: () => void) => void;
}) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState(user.username);
  const [editRole, setEditRole] = useState(user.role);
  const [saving, setSaving] = useState(false);
  const [devices, setDevices] = useState<RegisteredDevice[] | null>(null);
  const [devLoading, setDevLoading] = useState(false);
  const [changingPin, setChangingPin] = useState(false);
  const [newPin, setNewPin] = useState('');
  const [showNewPin, setShowNewPin] = useState(false);
  const [pinSaving, setPinSaving] = useState(false);

  const initial = user.username.slice(0, 1).toUpperCase();
  const canEditThis = isOwner || (canManage && user.role !== 'owner');

  const loadDevices = useCallback(async () => {
    setDevLoading(true);
    try {
      const res = await fetch(`${UM}/users/${user.user_id}/devices`, {
        headers: authHeaders(),
        credentials: 'include',
      });
      if (res.ok) {
        const d = await res.json();
        setDevices(d.devices ?? []);
      }
    } catch { /* ignore */ } finally {
      setDevLoading(false);
    }
  }, [user.user_id]);

  useEffect(() => {
    if (expanded && devices === null) loadDevices();
  }, [expanded, devices, loadDevices]);

  const saveEdit = () => {
    requestElevation(async () => {
      setSaving(true);
      await fetch(`${UM}/users/${user.user_id}`, {
        method: 'PATCH',
        headers: authHeaders(true),
        credentials: 'include',
        body: JSON.stringify({ username: editName, role: editRole }),
      });
      setSaving(false);
      setEditing(false);
      onRefresh();
    });
  };

  const savePin = () => {
    if (newPin.length < 4) return;
    requestElevation(async () => {
      setPinSaving(true);
      await fetch(`${UM}/users/${user.user_id}/pin`, {
        method: 'POST',
        headers: authHeaders(true),
        credentials: 'include',
        body: JSON.stringify({ pin: newPin }),
      });
      setPinSaving(false);
      setChangingPin(false);
      setNewPin('');
    });
  };

  const revokeDevice = async (deviceId: string) => {
    await fetch(`${UM}/devices/${deviceId}`, {
      method: 'DELETE',
      headers: authHeaders(true),
      credentials: 'include',
    });
    loadDevices();
  };

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900 overflow-hidden">
      {/* Row header */}
      <div className="flex items-center gap-3 px-4 py-3">
        {/* Avatar */}
        <div className="w-8 h-8 rounded-full bg-violet-600/30 flex items-center justify-center text-sm font-bold text-violet-300 shrink-0">
          {initial}
        </div>

        {/* Name + role */}
        {editing ? (
          <div className="flex items-center gap-2 flex-1 min-w-0">
            <input
              value={editName}
              onChange={(e) => setEditName(e.target.value)}
              className="bg-zinc-800 border border-zinc-700 rounded-lg px-2 py-1 text-sm text-zinc-50 focus:outline-none focus:border-violet-500 w-32"
            />
            {isOwner && user.role !== 'owner' && (
              <select
                value={editRole}
                onChange={(e) => setEditRole(e.target.value)}
                className="bg-zinc-800 border border-zinc-700 rounded-lg px-2 py-1 text-xs text-zinc-50 focus:outline-none focus:border-violet-500"
              >
                {['admin', 'user', 'guest'].map((r) => (
                  <option key={r} value={r}>{r}</option>
                ))}
              </select>
            )}
          </div>
        ) : (
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium truncate">{user.username}</span>
              <RoleBadge role={user.role} />
            </div>
          </div>
        )}

        {/* Actions */}
        <div className="flex items-center gap-1 shrink-0">
          {canEditThis && !editing && (
            <>
              <button
                onClick={() => { setEditing(true); setEditName(user.username); setEditRole(user.role); }}
                className="p-1.5 text-zinc-500 hover:text-zinc-300 transition-colors"
                title={t('usersPanel.editUser')}
              >
                <Edit3 size={13} />
              </button>
              <button
                onClick={() => setChangingPin((p) => !p)}
                className="p-1.5 text-zinc-500 hover:text-zinc-300 transition-colors"
                title={t('usersPanel.changePin')}
              >
                <Key size={13} />
              </button>
              <button
                onClick={onDelete}
                className="p-1.5 text-zinc-500 hover:text-red-400 transition-colors"
                title={t('usersPanel.deleteUser')}
              >
                <Trash2 size={13} />
              </button>
            </>
          )}
          {editing && (
            <>
              <button
                onClick={saveEdit}
                disabled={saving}
                className="p-1.5 text-emerald-400 hover:text-emerald-300 transition-colors"
              >
                <Check size={13} />
              </button>
              <button
                onClick={() => setEditing(false)}
                className="p-1.5 text-zinc-500 hover:text-zinc-300 transition-colors"
              >
                <X size={13} />
              </button>
            </>
          )}
          {/* Expand toggle */}
          <button
            onClick={onToggle}
            className="p-1.5 text-zinc-500 hover:text-zinc-300 transition-colors ml-1"
          >
            {expanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
          </button>
        </div>
      </div>

      {/* PIN change row */}
      {changingPin && (
        <div className="flex items-center gap-2 px-4 pb-3 border-t border-zinc-800 pt-2">
          <Key size={12} className="text-zinc-500 shrink-0" />
          <div className="relative">
            <input
              type={showNewPin ? 'text' : 'password'}
              inputMode="numeric"
              pattern="[0-9]*"
              maxLength={8}
              value={newPin}
              onChange={(e) => setNewPin(e.target.value.replace(/\D/g, ''))}
              placeholder="new PIN"
              className="bg-zinc-800 border border-zinc-700 rounded-lg px-2 py-1 pr-7 text-sm font-mono tracking-widest focus:outline-none focus:border-violet-500 w-28"
            />
            <button
              onClick={() => setShowNewPin((p) => !p)}
              className="absolute right-1.5 top-1/2 -translate-y-1/2 text-zinc-600 hover:text-zinc-400"
            >
              {showNewPin ? <EyeOff size={11} /> : <Eye size={11} />}
            </button>
          </div>
          <button
            onClick={savePin}
            disabled={pinSaving || newPin.length < 4}
            className="px-2 py-1 text-xs bg-violet-600 hover:bg-violet-500 text-white rounded-lg transition-colors disabled:opacity-50"
          >
            {pinSaving ? '…' : t('usersPanel.savePin')}
          </button>
          <button onClick={() => { setChangingPin(false); setNewPin(''); }} className="text-zinc-600 hover:text-zinc-400">
            <X size={12} />
          </button>
        </div>
      )}

      {/* Expanded: devices */}
      {expanded && (
        <div className="border-t border-zinc-800 px-4 py-3 bg-zinc-950/40">
          <div className="flex items-center justify-between mb-2">
            <span className="text-[11px] font-medium text-zinc-500 uppercase tracking-wide">
              <Smartphone size={10} className="inline mr-1" />
              {t('usersPanel.devices')}
            </span>
            <button onClick={loadDevices} className="text-zinc-600 hover:text-zinc-400">
              <RefreshCw size={11} />
            </button>
          </div>
          {devLoading ? (
            <p className="text-xs text-zinc-600">{t('common.loading')}</p>
          ) : !devices || devices.length === 0 ? (
            <p className="text-xs text-zinc-600">{t('usersPanel.noDevices')}</p>
          ) : (
            <div className="space-y-1">
              {devices.map((d) => (
                <div key={d.device_id} className="flex items-center gap-2 text-xs">
                  <Smartphone size={11} className="text-zinc-600 shrink-0" />
                  <span className="flex-1 truncate text-zinc-400">{d.device_name}</span>
                  <span className="text-zinc-600 shrink-0">{d.ip ?? '—'}</span>
                  <button
                    onClick={() => revokeDevice(d.device_id)}
                    className="text-zinc-600 hover:text-red-400 transition-colors shrink-0"
                    title={t('usersPanel.revokeDevice')}
                  >
                    <X size={11} />
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

// ─── Create user modal ────────────────────────────────────────────────────────
function CreateUserModal({
  isOwner, onClose, onCreated, requestElevation,
}: {
  isOwner: boolean;
  onClose: () => void;
  onCreated: () => void;
  requestElevation: (fn: () => void) => void;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState('');
  const [role, setRole] = useState('user');
  const [pin, setPin] = useState('');
  const [showPin, setShowPin] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = () => {
    if (!name || pin.length < 4) return;
    requestElevation(async () => {
      setSaving(true);
      setError(null);
      try {
        const res = await fetch(`${UM}/users`, {
          method: 'POST',
          headers: authHeaders(true),
          credentials: 'include',
          body: JSON.stringify({ username: name, role, pin }),
        });
        if (!res.ok) {
          const d = await res.json().catch(() => ({}));
          setError(d.detail ?? t('common.error'));
          setSaving(false);
          return;
        }
        onCreated();
      } catch {
        setError(t('common.error'));
        setSaving(false);
      }
    });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div className="relative z-10 w-full max-w-sm bg-zinc-900 border border-zinc-800 rounded-xl shadow-2xl p-6 space-y-4">
        <div className="flex items-center gap-2">
          <Users size={16} className="text-violet-400" />
          <h4 className="text-sm font-semibold">{t('usersPanel.createUser')}</h4>
          <button onClick={onClose} className="ml-auto text-zinc-600 hover:text-zinc-300"><X size={14} /></button>
        </div>

        <div className="space-y-3">
          <Field label={t('auth.username')}>
            <input
              value={name}
              onChange={(e) => { setName(e.target.value); setError(null); }}
              className="w-full bg-zinc-950 border border-zinc-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-violet-500"
              placeholder={t('auth.usernamePlaceholder')}
            />
          </Field>

          {isOwner && (
            <Field label={t('usersPanel.role')}>
              <select
                value={role}
                onChange={(e) => setRole(e.target.value)}
                className="w-full bg-zinc-950 border border-zinc-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-violet-500"
              >
                {['admin', 'user', 'guest'].map((r) => (
                  <option key={r} value={r}>{r}</option>
                ))}
              </select>
            </Field>
          )}

          <Field label={t('auth.pin')}>
            <div className="relative">
              <input
                type={showPin ? 'text' : 'password'}
                inputMode="numeric"
                pattern="[0-9]*"
                maxLength={8}
                value={pin}
                onChange={(e) => { setPin(e.target.value.replace(/\D/g, '')); setError(null); }}
                className={cn(
                  'w-full bg-zinc-950 border rounded-lg px-3 py-2 pr-9 text-sm font-mono tracking-widest focus:outline-none transition-all',
                  error ? 'border-red-500' : 'border-zinc-700 focus:border-violet-500',
                )}
                placeholder="••••"
              />
              <button
                type="button"
                onClick={() => setShowPin((p) => !p)}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-zinc-600 hover:text-zinc-400"
              >
                {showPin ? <EyeOff size={13} /> : <Eye size={13} />}
              </button>
            </div>
          </Field>
        </div>

        {error && (
          <div className="flex items-center gap-2 text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">
            <AlertCircle size={12} className="shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <button
          onClick={submit}
          disabled={saving || !name || pin.length < 4}
          className="w-full py-2.5 rounded-lg bg-violet-600 hover:bg-violet-500 text-white text-sm font-medium transition-colors disabled:opacity-50"
        >
          {saving ? '…' : t('usersPanel.createBtn')}
        </button>
      </div>
    </div>
  );
}

// ═════════════════════════════════════════════════════════════════════════════
//  Roles permissions editor (owner only)
// ═════════════════════════════════════════════════════════════════════════════
const BOOL_PERMS: (keyof RolePerms)[] = [
  'devices_view', 'devices_control', 'modules_configure',
  'users_manage', 'system_reboot', 'system_update', 'integrity_logs_view',
];

const SELECT_PERMS: { key: keyof RolePerms; options: string[] }[] = [
  { key: 'scenes_run', options: ['all', 'approved', 'none'] },
  { key: 'voice_commands', options: ['all', 'basic', 'none'] },
];

function RolesEditor() {
  const { t } = useTranslation();
  const [roles, setRoles] = useState<string[]>([]);
  const [selected, setSelected] = useState('admin');
  const [perms, setPerms] = useState<RolePerms | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const { requestElevation, pinModalProps } = useElevated();

  useEffect(() => {
    fetch(`${UM}/roles`, { headers: authHeaders(), credentials: 'include' })
      .then((r) => r.json())
      .then((d) => {
        const rs: string[] = (d.roles ?? []).map((r: { role: string }) => r.role).filter((r: string) => r !== 'owner');
        setRoles(rs);
        if (rs.length > 0) setSelected(rs[0]);
      })
      .catch(() => { });
  }, []);

  useEffect(() => {
    if (!selected) return;
    setLoading(true);
    setPerms(null);
    fetch(`${UM}/roles/${selected}/permissions`, { headers: authHeaders(), credentials: 'include' })
      .then((r) => r.json())
      .then((d) => setPerms(d.permissions ?? null))
      .catch(() => { })
      .finally(() => setLoading(false));
  }, [selected]);

  const toggle = (key: keyof RolePerms) => {
    if (!perms) return;
    setPerms({ ...perms, [key]: !perms[key] });
  };
  const setSelect = (key: keyof RolePerms, val: string) => {
    if (!perms) return;
    setPerms({ ...perms, [key]: val });
  };

  const save = () => {
    if (!perms) return;
    requestElevation(async () => {
      setSaving(true);
      await fetch(`${UM}/roles/${selected}/permissions`, {
        method: 'PUT',
        headers: authHeaders(true),
        credentials: 'include',
        body: JSON.stringify({ permissions: perms }),
      });
      setSaving(false);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    });
  };

  return (
    <div className="space-y-4">
      {/* Role selector tabs */}
      <div className="flex gap-1 p-1 bg-zinc-900 rounded-lg w-fit border border-zinc-800">
        {roles.map((r) => (
          <button
            key={r}
            onClick={() => setSelected(r)}
            className={cn(
              'px-3 py-1.5 text-xs font-medium rounded-md transition-all',
              selected === r ? 'bg-zinc-700 text-zinc-50' : 'text-zinc-500 hover:text-zinc-300',
            )}
          >
            {r}
          </button>
        ))}
      </div>

      {loading && <Spinner />}
      {!loading && perms && (
        <div className="p-4 bg-zinc-900 border border-zinc-800 rounded-xl space-y-3">
          {/* Boolean toggles */}
          {BOOL_PERMS.map((key) => (
            <div key={key} className="flex items-center justify-between gap-3">
              <span className="text-sm text-zinc-300">{t(`usersPanel.perm_${key}`)}</span>
              <button
                onClick={() => toggle(key)}
                className={cn(
                  'relative w-9 h-5 rounded-full transition-colors',
                  perms[key] ? 'bg-violet-600' : 'bg-zinc-700',
                )}
              >
                <span className={cn(
                  'absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform',
                  perms[key] ? 'translate-x-4' : 'translate-x-0.5',
                )} />
              </button>
            </div>
          ))}

          {/* Select dropdowns */}
          {SELECT_PERMS.map(({ key, options }) => (
            <div key={key} className="flex items-center justify-between gap-3">
              <span className="text-sm text-zinc-300">{t(`usersPanel.perm_${key}`)}</span>
              <select
                value={perms[key] as string}
                onChange={(e) => setSelect(key, e.target.value)}
                className="bg-zinc-800 border border-zinc-700 rounded-lg px-2 py-1 text-xs focus:outline-none focus:border-violet-500"
              >
                {options.map((o) => <option key={o} value={o}>{o}</option>)}
              </select>
            </div>
          ))}

          <div className="pt-2 border-t border-zinc-800">
            <button
              onClick={save}
              disabled={saving}
              className="flex items-center gap-1.5 px-4 py-2 text-sm font-medium text-white bg-violet-600 hover:bg-violet-500 rounded-lg transition-colors disabled:opacity-50"
            >
              {saved ? <><Check size={13} /> {t('usersPanel.saved')}</> : saving ? '…' : <><Shield size={13} /> {t('usersPanel.savePermissions')}</>}
            </button>
          </div>
        </div>
      )}

      <PinConfirmModal {...pinModalProps} />
    </div>
  );
}

// ─── tiny helpers ─────────────────────────────────────────────────────────────
function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <label className="block text-xs font-medium text-zinc-400 mb-1">{label}</label>
      {children}
    </div>
  );
}

function Spinner() {
  return (
    <div className="flex justify-center py-6">
      <div className="w-5 h-5 border-2 border-violet-500 border-t-transparent rounded-full animate-spin" />
    </div>
  );
}

function ErrorMsg({ msg, onRetry }: { msg: string; onRetry: () => void }) {
  return (
    <div className="flex items-center gap-3 p-3 bg-red-500/10 border border-red-500/20 rounded-lg text-xs text-red-400">
      <AlertCircle size={13} className="shrink-0" />
      <span className="flex-1">{msg}</span>
      <button onClick={onRetry} className="flex items-center gap-1 hover:text-red-300">
        <RefreshCw size={11} /> retry
      </button>
    </div>
  );
}
