/**
 * UsersPanel — full user management UI for Settings → Users tab.
 * Talks to /api/ui/modules/user-manager/ endpoints.
 * Auth: X-Device-Token header + X-Elevated-Token for sensitive ops.
 */
import { useState, useEffect, useCallback, useRef, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import {
    Users, Plus, Trash2, Edit3, Key, Smartphone, QrCode,
    ChevronDown, ChevronUp, Shield, Check, X, AlertCircle,
    Eye, EyeOff, RefreshCw, MapPin, Link, Wifi, Bell, Send, Smile,
    Info, AlertTriangle, Siren,
} from 'lucide-react';
import { cn } from '../lib/utils';
import { useStore } from '../store/useStore';
import { useElevated } from '../hooks/useElevated';
import PinConfirmModal from './PinConfirmModal';

const UM = '/api/ui/modules/user-manager';
const PD = '/api/ui/modules/presence-detection';
const NR = '/api/ui/modules/notification-router';

function getToken() {
    return sessionStorage.getItem('selena_session') ?? localStorage.getItem('selena_device') ?? undefined;
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

function PresenceBadge({ state, awaySec }: { state: string; awaySec: number | null }) {
    const { t } = useTranslation();
    if (state === 'home') {
        return (
            <span className="flex items-center gap-1 text-[10px] font-medium text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 px-1.5 py-0.5 rounded">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
                {t('usersPanel.presenceHome')}
            </span>
        );
    }
    if (state === 'away') {
        return (
            <span className="flex items-center gap-1 text-[10px] font-medium text-zinc-500 bg-zinc-800 border border-zinc-700 px-1.5 py-0.5 rounded">
                <MapPin size={9} />
                {t('usersPanel.presenceAway')}
            </span>
        );
    }
    if (awaySec !== null && awaySec > 0) {
        const min = Math.ceil(awaySec / 60);
        return (
            <span className="flex items-center gap-1 text-[10px] font-medium text-amber-400 bg-amber-500/10 border border-amber-500/20 px-1.5 py-0.5 rounded">
                <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
                {t('usersPanel.presenceLeaving', { min })}
            </span>
        );
    }
    return null;
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

interface PresenceUser {
    user_id: string;
    name: string;
    devices: { type: string; address: string }[];
    linked_account_id: string | null;
    state: 'home' | 'away' | 'unknown';
    last_seen: string | null;
    away_in_sec: number | null;
    confidence: number;
    detected: boolean;
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
    const [presenceUsers, setPresenceUsers] = useState<PresenceUser[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [showCreate, setShowCreate] = useState(false);
    const [expandedId, setExpandedId] = useState<string | null>(null);
    const { requestElevation, pinModalProps } = useElevated();

    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const [accRes, presRes] = await Promise.all([
                fetch(`${UM}/users`, { headers: authHeaders(), credentials: 'include' }),
                fetch(`${PD}/users`),
            ]);
            if (!accRes.ok) throw new Error();
            const accData = await accRes.json();
            const userList: UserProfile[] = accData.users ?? [];
            setUsers(userList);
            // auto-expand first user on first load
            setExpandedId((prev) => prev ?? (userList[0]?.user_id ?? null));
            if (presRes.ok) {
                const presData = await presRes.json();
                setPresenceUsers(presData.users ?? []);
            }
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
        // kept for QR device login (not used in create flow anymore)
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
                    <button
                        onClick={() => setShowCreate(true)}
                        className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-white bg-violet-600 hover:bg-violet-500 rounded-lg transition-colors"
                    >
                        <Plus size={13} />
                        {t('usersPanel.addUser')}
                    </button>
                )}
            </div>

            {/* User rows */}
            {users.length === 0 ? (
                <p className="text-sm text-zinc-500 py-4">{t('usersPanel.noUsers')}</p>
            ) : (
                <div className="space-y-2">
                    {users.map((u) => {
                        const linked = presenceUsers.find(p => p.linked_account_id === u.user_id) ?? null;
                        return (
                            <UserRow
                                key={u.user_id}
                                user={u}
                                presenceUser={linked}
                                isOwner={isOwner}
                                canManage={canManage}
                                expanded={expandedId === u.user_id}
                                onToggle={() => setExpandedId((id) => id === u.user_id ? null : u.user_id)}
                                onDelete={() => deleteUser(u)}
                                onRefresh={load}
                                requestElevation={requestElevation}
                            />
                        );
                    })}
                </div>
            )}

            {showCreate && (
                <CreateUserModal
                    isOwner={isOwner}
                    onClose={() => setShowCreate(false)}
                    onCreated={() => { setShowCreate(false); load(); }}
                />
            )}

            <PinConfirmModal {...pinModalProps} />
        </div>
    );
}

// ─── Single user row ──────────────────────────────────────────────────────────
function UserRow({
    user, presenceUser, isOwner, canManage, expanded, onToggle, onDelete, onRefresh, requestElevation,
}: {
    user: UserProfile;
    presenceUser: PresenceUser | null;
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
    const [activeDetailTab, setActiveDetailTab] = useState<'devices' | 'notify'>('devices');
    const [notifyMsg, setNotifyMsg] = useState('');
    const [notifyLevel, setNotifyLevel] = useState<'info' | 'warning' | 'critical'>('info');
    const [notifySending, setNotifySending] = useState(false);
    const [notifySent, setNotifySent] = useState(false);
    const [emojiOpen, setEmojiOpen] = useState(false);
    const notifyRef = useRef<HTMLTextAreaElement>(null);
    const [presenceQr, setPresenceQr] = useState<{ qr_svg: string; join_url: string } | null>(null);
    const [setupTrackingQr, setSetupTrackingQr] = useState<{ qr_svg: string; join_url: string } | null>(null);

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
            const isSelf = isOwner && user.role === 'owner';
            await fetch(`${UM}/users/${user.user_id}/pin`, {
                method: 'POST',
                headers: authHeaders(true),
                credentials: 'include',
                body: JSON.stringify({ current_pin: isSelf ? newPin : '', new_pin: newPin }),
            });
            setPinSaving(false);
            setChangingPin(false);
            setNewPin('');
        });
    };

    const sendNotify = async () => {
        if (!notifyMsg.trim()) return;
        setNotifySending(true);
        try {
            await fetch(`${NR}/notify/send`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message: `[${user.username}] ${notifyMsg}`,
                    level: notifyLevel,
                    tags: ['manual', `user:${user.user_id}`],
                }),
            });
            setNotifyMsg('');
            setNotifySent(true);
            setTimeout(() => setNotifySent(false), 2000);
        } catch { /* ignore */ } finally {
            setNotifySending(false);
        }
    };

    const revokeDevice = async (deviceId: string) => {
        await fetch(`${UM}/devices/${deviceId}`, {
            method: 'DELETE',
            headers: authHeaders(true),
            credentials: 'include',
        });
        loadDevices();
    };

    const renameDevice = async (deviceId: string, newName: string) => {
        if (!newName.trim()) return;
        await fetch(`${UM}/devices/${deviceId}`, {
            method: 'PATCH',
            headers: authHeaders(true),
            credentials: 'include',
            body: JSON.stringify({ device_name: newName.trim() }),
        });
        loadDevices();
    };

    const addPresenceDevice = async () => {
        if (!presenceUser) return;
        const res = await fetch(`${PD}/users/${encodeURIComponent(presenceUser.user_id)}/invite`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: presenceUser.name, base_url: '' }),
        });
        if (res.ok) {
            const d = await res.json();
            setPresenceQr({ qr_svg: d.qr_svg, join_url: d.join_url });
        }
    };

    const deletePresence = async () => {
        if (!presenceUser) return;
        if (!confirm(`${t('usersPanel.presenceDeleteUser')} "${presenceUser.name}"?`)) return;
        await fetch(`${PD}/users/${encodeURIComponent(presenceUser.user_id)}`, { method: 'DELETE' });
        onRefresh();
    };

    const setupTracking = async () => {
        const res = await fetch(`${PD}/accounts/${encodeURIComponent(user.user_id)}/invite`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: user.username, base_url: '' }),
        });
        if (res.ok) {
            const d = await res.json();
            setSetupTrackingQr({ qr_svg: d.qr_svg, join_url: d.join_url });
        }
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
                        <div className="flex items-center gap-2 flex-wrap">
                            <span className="text-sm font-medium truncate">{user.username}</span>
                            <RoleBadge role={user.role} />
                            {presenceUser && (
                                <PresenceBadge state={presenceUser.state} awaySec={presenceUser.away_in_sec} />
                            )}
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

            {/* Expanded: details */}
            {expanded && (
                <div className="border-t border-zinc-800">
                    {/* Tab bar */}
                    <div className="flex border-b border-zinc-800 bg-zinc-950/30">
                        {(['devices', 'notify'] as const).map((tab) => (
                            <button
                                key={tab}
                                onClick={() => setActiveDetailTab(tab)}
                                className={cn(
                                    'flex items-center gap-1.5 px-4 py-2.5 text-xs font-medium border-b-2 -mb-px transition-colors',
                                    activeDetailTab === tab
                                        ? 'border-violet-500 text-violet-400'
                                        : 'border-transparent text-zinc-500 hover:text-zinc-300',
                                )}
                            >
                                {tab === 'devices' ? <Smartphone size={11} /> : <Bell size={11} />}
                                {t(`usersPanel.tab_detail_${tab}`)}
                            </button>
                        ))}
                    </div>

                    {/* Devices tab */}
                    {activeDetailTab === 'devices' && (
                        <div className="px-4 py-3 bg-zinc-950/40 space-y-3">
                            <div className="flex items-center justify-between">
                                <span className="text-[11px] font-medium text-zinc-500 uppercase tracking-wide flex items-center gap-1">
                                    <Smartphone size={10} />
                                    {t('usersPanel.registeredSessions')}
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
                                <div className="space-y-1.5">
                                    {devices.map((d) => (
                                        <DeviceRow
                                            key={d.device_id}
                                            device={d}
                                            onRevoke={() => revokeDevice(d.device_id)}
                                            onRename={(name) => renameDevice(d.device_id, name)}
                                        />
                                    ))}
                                </div>
                            )}

                            {/* Presence tracking */}
                            {presenceUser && (
                                <div className="pt-2 border-t border-zinc-800/60">
                                    <div className="flex items-center gap-1 mb-1.5">
                                        <Wifi size={9} className="text-zinc-600" />
                                        <span className="text-[10px] font-medium text-zinc-600 uppercase tracking-wide flex-1">
                                            {t('usersPanel.presenceDevices')}
                                        </span>
                                        {canManage && (
                                            <>
                                                <button
                                                    onClick={addPresenceDevice}
                                                    className="p-0.5 text-zinc-600 hover:text-violet-400 transition-colors"
                                                    title={t('usersPanel.presenceDeviceQr')}
                                                >
                                                    <QrCode size={10} />
                                                </button>
                                                <button
                                                    onClick={deletePresence}
                                                    className="p-0.5 text-zinc-600 hover:text-red-400 transition-colors"
                                                    title={t('usersPanel.presenceDeleteUser')}
                                                >
                                                    <Trash2 size={10} />
                                                </button>
                                            </>
                                        )}
                                    </div>
                                    {presenceQr && <PresenceQrModal qrSvg={presenceQr.qr_svg} onClose={() => setPresenceQr(null)} />}
                                    <div className="space-y-1">
                                        {presenceUser.devices.map((d, i) => (
                                            <div key={i} className="flex items-center gap-2 text-xs bg-zinc-900 rounded-lg px-3 py-2">
                                                <Wifi size={11} className="text-violet-500/60 shrink-0" />
                                                <span className="text-zinc-500 uppercase text-[10px] w-7 shrink-0">{d.type}</span>
                                                <span className="flex-1 font-mono text-zinc-400 truncate">{d.address}</span>
                                                <PresenceBadge state={presenceUser.state} awaySec={presenceUser.away_in_sec} />
                                            </div>
                                        ))}
                                        {presenceUser.devices.length === 0 && (
                                            <p className="text-[11px] text-zinc-600 italic">{t('usersPanel.noTrackingDevices')}</p>
                                        )}
                                    </div>
                                </div>
                            )}
                            {!presenceUser && canManage && (
                                <div className="pt-2 border-t border-zinc-800/60">
                                    <div className="flex items-center gap-1 mb-1.5">
                                        <Wifi size={9} className="text-zinc-600" />
                                        <span className="text-[10px] font-medium text-zinc-600 uppercase tracking-wide flex-1">
                                            {t('usersPanel.presenceDevices')}
                                        </span>
                                        <button
                                            onClick={setupTracking}
                                            className="flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-medium text-zinc-500 hover:text-violet-400 bg-zinc-800/60 hover:bg-zinc-800 rounded transition-colors"
                                            title={t('usersPanel.presenceSetupTracking')}
                                        >
                                            <QrCode size={9} />
                                            {t('usersPanel.presenceSetupTracking')}
                                        </button>
                                    </div>
                                    {setupTrackingQr && <PresenceQrModal qrSvg={setupTrackingQr.qr_svg} onClose={() => setSetupTrackingQr(null)} />}
                                </div>
                            )}
                        </div>
                    )}

                    {/* Notify tab */}
                    {activeDetailTab === 'notify' && (
                        <div className="px-4 py-3 bg-zinc-950/40 space-y-3">
                            <p className="text-[11px] text-zinc-500">{t('usersPanel.notifyDesc')}</p>
                            <div className="relative">
                                <textarea
                                    ref={notifyRef}
                                    value={notifyMsg}
                                    onChange={(e) => setNotifyMsg(e.target.value)}
                                    placeholder={t('usersPanel.notifyPlaceholder')}
                                    rows={3}
                                    className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 pr-9 text-sm text-zinc-100 resize-none focus:outline-none focus:border-violet-500"
                                />
                                <button
                                    type="button"
                                    onClick={() => setEmojiOpen(!emojiOpen)}
                                    className="absolute right-2 top-2 p-0.5 text-zinc-500 hover:text-zinc-300 transition-colors"
                                    title="Emoji"
                                >
                                    <Smile size={16} />
                                </button>
                                {emojiOpen && (
                                    <div className="absolute right-0 bottom-full mb-1 z-50 bg-zinc-800 border border-zinc-700 rounded-lg p-2 shadow-xl grid grid-cols-8 gap-1 w-72">
                                        {['😀', '😂', '😍', '😎', '🥳', '😢', '😡', '🔥', '✅', '❌', '⚡', '💡', '🔔', '📢', '🏠', '🌡️', '💧', '🎉', '👋', '❤️', '⭐', '🚀', '🛡️', '⏰', '🔒', '📱', '💻', '🎵', '☀️', '🌙', '🌧️', '❄️'].map((e) => (
                                            <button
                                                key={e}
                                                type="button"
                                                onClick={() => {
                                                    const ta = notifyRef.current;
                                                    if (ta) {
                                                        const s = ta.selectionStart ?? notifyMsg.length;
                                                        setNotifyMsg(notifyMsg.slice(0, s) + e + notifyMsg.slice(s));
                                                        setTimeout(() => { ta.focus(); ta.selectionStart = ta.selectionEnd = s + e.length; }, 0);
                                                    } else {
                                                        setNotifyMsg(notifyMsg + e);
                                                    }
                                                    setEmojiOpen(false);
                                                }}
                                                className="text-lg hover:bg-zinc-700 rounded p-0.5 leading-none transition-colors"
                                            >
                                                {e}
                                            </button>
                                        ))}
                                    </div>
                                )}
                            </div>
                            <div className="flex items-center gap-2">
                                {([
                                    { lvl: 'info' as const, icon: <Info size={11} />, emoji: 'ℹ️' },
                                    { lvl: 'warning' as const, icon: <AlertTriangle size={11} />, emoji: '⚠️' },
                                    { lvl: 'critical' as const, icon: <Siren size={11} />, emoji: '🚨' },
                                ]).map(({ lvl, icon }) => (
                                    <button
                                        key={lvl}
                                        onClick={() => setNotifyLevel(lvl)}
                                        className={cn(
                                            'flex items-center gap-1 px-2.5 py-1 text-[11px] font-medium rounded border transition-colors',
                                            notifyLevel === lvl
                                                ? lvl === 'info'
                                                    ? 'bg-blue-500/20 border-blue-500/40 text-blue-300'
                                                    : lvl === 'warning'
                                                        ? 'bg-amber-500/20 border-amber-500/40 text-amber-300'
                                                        : 'bg-red-500/20 border-red-500/40 text-red-300'
                                                : 'bg-zinc-800 border-zinc-700 text-zinc-500',
                                        )}
                                    >
                                        {icon} {lvl}
                                    </button>
                                ))}
                                <button
                                    onClick={sendNotify}
                                    disabled={!notifyMsg.trim() || notifySending}
                                    className="ml-auto flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-violet-600 hover:bg-violet-500 text-white rounded-lg disabled:opacity-50 transition-colors"
                                >
                                    {notifySent ? (
                                        <><Check size={11} /> {t('usersPanel.notifySent')}</>
                                    ) : (
                                        <><Send size={11} /> {notifySending ? '…' : t('usersPanel.sendNotify')}</>
                                    )}
                                </button>
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}

// ─── Device row with inline rename ───────────────────────────────────────────
function DeviceRow({
    device, onRevoke, onRename,
}: {
    device: RegisteredDevice;
    onRevoke: () => void;
    onRename: (name: string) => void;
}) {
    const { t } = useTranslation();
    const [editing, setEditing] = useState(false);
    const [val, setVal] = useState(device.device_name);

    const save = () => {
        onRename(val);
        setEditing(false);
    };

    return (
        <div className="flex items-center gap-2 text-xs bg-zinc-900 rounded-lg px-3 py-2">
            <Smartphone size={11} className="text-zinc-500 shrink-0" />
            <div className="flex-1 min-w-0">
                {editing ? (
                    <input
                        value={val}
                        onChange={(e) => setVal(e.target.value)}
                        onKeyDown={(e) => { if (e.key === 'Enter') save(); if (e.key === 'Escape') { setVal(device.device_name); setEditing(false); } }}
                        autoFocus
                        className="w-full bg-zinc-800 border border-violet-500/50 rounded px-1.5 py-0.5 text-xs text-zinc-100 focus:outline-none"
                    />
                ) : (
                    <>
                        <p className="truncate text-zinc-300 text-[12px]">{device.device_name}</p>
                        <p className="text-zinc-600 text-[10px]">{device.ip ?? '—'}</p>
                    </>
                )}
            </div>
            <span className={cn(
                'text-[10px] px-1.5 py-0.5 rounded border shrink-0',
                device.active ? 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20' : 'text-zinc-600 bg-zinc-800 border-zinc-700',
            )}>
                {device.active ? 'active' : 'revoked'}
            </span>
            {device.active && (
                <>
                    {editing ? (
                        <>
                            <button onClick={save} className="text-emerald-400 hover:text-emerald-300 shrink-0 p-1"><Check size={11} /></button>
                            <button onClick={() => { setVal(device.device_name); setEditing(false); }} className="text-zinc-600 hover:text-zinc-400 shrink-0 p-1"><X size={11} /></button>
                        </>
                    ) : (
                        <button onClick={() => setEditing(true)} className="text-zinc-600 hover:text-zinc-300 transition-colors shrink-0 p-1" title={t('usersPanel.editDevice')}><Edit3 size={11} /></button>
                    )}
                    <button onClick={onRevoke} className="text-zinc-600 hover:text-red-400 transition-colors shrink-0 p-1" title={t('usersPanel.revokeDevice')}><X size={11} /></button>
                </>
            )}
        </div>
    );
}

// ─── Create user modal ────────────────────────────────────────────────────────
function CreateUserModal({
    isOwner, onClose, onCreated,
}: {
    isOwner: boolean;
    onClose: () => void;
    onCreated: () => void;
}) {
    const { t } = useTranslation();
    const [role, setRole] = useState('user');
    const [loading, setLoading] = useState(false);
    const [qrData, setQrData] = useState<{ session_id: string; qr_image: string | null } | null>(null);
    const [status, setStatus] = useState<'idle' | 'waiting' | 'done' | 'expired'>('idle');

    // Poll for completion
    useEffect(() => {
        if (!qrData || status !== 'waiting') return;
        const iv = setInterval(async () => {
            try {
                const res = await fetch(`${UM}/auth/qr/status/${qrData.session_id}`, {
                    headers: { 'Content-Type': 'application/json' },
                });
                if (res.status === 404 || res.status === 410) {
                    setStatus('expired');
                    clearInterval(iv);
                    return;
                }
                const d = await res.json();
                if (d.status === 'complete') {
                    setStatus('done');
                    clearInterval(iv);
                    setTimeout(onCreated, 1200);
                }
            } catch { /* ignore */ }
        }, 2000);
        return () => clearInterval(iv);
    }, [qrData, status, onCreated]);

    const generate = async () => {
        setLoading(true);
        try {
            const res = await fetch(`${UM}/auth/qr/start`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', ...authHeaders() },
                credentials: 'include',
                body: JSON.stringify({ mode: 'invite', role }),
            });
            if (res.ok) {
                const d = await res.json();
                setQrData({ session_id: d.session_id, qr_image: d.qr_image });
                setStatus('waiting');
            }
        } catch { /* ignore */ } finally {
            setLoading(false);
        }
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

                {status === 'idle' && (
                    <>
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
                        <p className="text-[12px] text-zinc-500">
                            {t('usersPanel.createQrWaiting').replace('…', '').trim() + ' — '}
                            {t('usersPanel.role')}: <span className="text-zinc-300 font-medium">{role}</span>
                        </p>
                        <button
                            onClick={generate}
                            disabled={loading}
                            className="w-full py-2.5 rounded-lg bg-violet-600 hover:bg-violet-500 text-white text-sm font-medium transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
                        >
                            <QrCode size={15} />
                            {loading ? '…' : t('usersPanel.createBtn')}
                        </button>
                    </>
                )}

                {(status === 'waiting' || status === 'done') && qrData && (
                    <div className="flex flex-col items-center gap-4">
                        {qrData.qr_image ? (
                            <img
                                src={qrData.qr_image}
                                alt="QR"
                                className="w-56 h-56 rounded-xl bg-white p-2"
                            />
                        ) : (
                            <div className="w-56 h-56 rounded-xl bg-zinc-800 flex items-center justify-center text-zinc-500 text-xs">
                                QR unavailable
                            </div>
                        )}
                        {status === 'waiting' && (
                            <p className="text-[12px] text-zinc-400 text-center animate-pulse">
                                {t('usersPanel.createQrWaiting')}
                            </p>
                        )}
                        {status === 'done' && (
                            <p className="text-[12px] text-green-400 text-center font-medium">
                                ✓ {t('usersPanel.createQrDone')}
                            </p>
                        )}
                        <button
                            onClick={() => { setQrData(null); setStatus('idle'); }}
                            className="text-xs text-zinc-600 hover:text-zinc-400 underline"
                        >
                            {t('common.cancel')}
                        </button>
                    </div>
                )}

                {status === 'expired' && (
                    <div className="text-center space-y-3">
                        <p className="text-sm text-red-400">{t('usersPanel.createQrExpired')}</p>
                        <button
                            onClick={() => { setQrData(null); setStatus('idle'); }}
                            className="text-xs text-zinc-500 hover:text-zinc-300 underline"
                        >
                            {t('common.cancel')}
                        </button>
                    </div>
                )}
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

// ─── QR modal for presence invites ──────────────────────────────────────────
function PresenceQrModal({ qrSvg, onClose }: { qrSvg: string; onClose: () => void }) {
    const { t } = useTranslation();
    return (
        <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
            onClick={onClose}
        >
            <div
                className="relative bg-zinc-900 border border-violet-500/40 rounded-2xl p-7 flex flex-col items-center gap-4 shadow-2xl"
                onClick={(e) => e.stopPropagation()}
            >
                <button
                    onClick={onClose}
                    className="absolute top-3 right-3 text-zinc-500 hover:text-zinc-200 transition-colors"
                >
                    <X size={16} />
                </button>
                <p className="text-sm font-semibold text-zinc-100">{t('usersPanel.presenceQrTitle')}</p>
                <div
                    className="w-60 h-60 bg-white rounded-xl p-2 overflow-hidden"
                    dangerouslySetInnerHTML={{ __html: qrSvg }}
                />
            </div>
        </div>
    );
}

// ─── Unlinked presence row ────────────────────────────────────────────────────
function UnlinkedPresenceRow({
    presenceUser, accounts, canManage, onRefresh,
}: {
    presenceUser: PresenceUser;
    accounts: UserProfile[];
    canManage: boolean;
    onRefresh: () => void;
}) {
    const { t } = useTranslation();
    const [linking, setLinking] = useState(false);
    const [selectedAccount, setSelectedAccount] = useState('');
    const [qr, setQr] = useState<{ qr_svg: string; join_url: string } | null>(null);
    const [deleting, setDeleting] = useState(false);

    const doLink = async () => {
        if (!selectedAccount) return;
        setLinking(true);
        try {
            await fetch(`${PD}/users/${encodeURIComponent(presenceUser.user_id)}/link`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ account_id: selectedAccount }),
            });
            onRefresh();
        } catch { /* ignore */ } finally {
            setLinking(false);
        }
    };

    const addDevice = async () => {
        const res = await fetch(`${PD}/users/${encodeURIComponent(presenceUser.user_id)}/invite`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: presenceUser.name, base_url: '' }),
        });
        if (res.ok) {
            const d = await res.json();
            setQr({ qr_svg: d.qr_svg, join_url: d.join_url });
        }
    };

    const deleteUser = async () => {
        if (!confirm(`${t('usersPanel.presenceDeleteUser')} "${presenceUser.name}"?`)) return;
        setDeleting(true);
        try {
            await fetch(`${PD}/users/${encodeURIComponent(presenceUser.user_id)}`, { method: 'DELETE' });
            onRefresh();
        } catch { /* ignore */ } finally {
            setDeleting(false);
        }
    };

    const initial = presenceUser.name.slice(0, 1).toUpperCase();
    const macDevices = presenceUser.devices.filter(d => d.type === 'mac');

    return (
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 overflow-hidden">
            {/* Header row */}
            <div className="px-4 py-3 flex items-center gap-3">
                <div className="w-8 h-8 rounded-full bg-zinc-700/50 flex items-center justify-center text-sm font-bold text-zinc-400 shrink-0">
                    {initial}
                </div>
                <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                        <span className="text-sm font-medium text-zinc-300 truncate">{presenceUser.name}</span>
                        <PresenceBadge state={presenceUser.state} awaySec={presenceUser.away_in_sec} />
                    </div>
                    {macDevices.length > 0 && (
                        <p className="text-[11px] text-zinc-600 mt-0.5 truncate">
                            {macDevices.map(d => d.address).join(', ')}
                        </p>
                    )}
                </div>
                {canManage && (
                    <div className="flex items-center gap-2 shrink-0">
                        <select
                            value={selectedAccount}
                            onChange={(e) => setSelectedAccount(e.target.value)}
                            className="bg-zinc-800 border border-zinc-700 rounded-lg px-2 py-1 text-xs text-zinc-50 focus:outline-none focus:border-violet-500"
                        >
                            <option value="">{t('usersPanel.linkSelect')}</option>
                            {accounts.map((a) => (
                                <option key={a.user_id} value={a.user_id}>{a.username}</option>
                            ))}
                        </select>
                        <button
                            onClick={doLink}
                            disabled={!selectedAccount || linking}
                            className="flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium text-white bg-violet-600 hover:bg-violet-500 rounded-lg disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                        >
                            <Link size={11} />
                            {t('usersPanel.linkBtn')}
                        </button>
                        <button
                            onClick={addDevice}
                            className="p-1.5 text-zinc-600 hover:text-violet-400 transition-colors"
                            title={t('usersPanel.presenceDeviceQr')}
                        >
                            <QrCode size={13} />
                        </button>
                        <button
                            onClick={deleteUser}
                            disabled={deleting}
                            className="p-1.5 text-zinc-600 hover:text-red-400 transition-colors"
                            title={t('usersPanel.presenceDeleteUser')}
                        >
                            <Trash2 size={13} />
                        </button>
                    </div>
                )}
            </div>
            {/* QR modal */}
            {qr && <PresenceQrModal qrSvg={qr.qr_svg} onClose={() => setQr(null)} />}
        </div>
    );
}

// ─── Roles editor ─────────────────────────────────────────────────────────────
function RolesEditor() {
    const { t } = useTranslation();
    const [roles, setRoles] = useState<string[]>([]);
    const [selected, setSelected] = useState<string>('');
    const [perms, setPerms] = useState<RolePerms | null>(null);
    const [loading, setLoading] = useState(false);
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);
    const { requestElevation, pinModalProps } = useElevated();

    useEffect(() => {
        fetch(`${UM}/roles`, { headers: authHeaders(), credentials: 'include' })
            .then((r) => r.json())
            .then((d) => {
                const rs = Object.keys(d).filter((r) => r !== 'owner');
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
            .then((d) => setPerms(d as RolePerms))
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
