import { useTranslation } from 'react-i18next';
import { useMemo } from 'react';
import type { Module } from '../../store/useStore';

/** Sentinel room values:
 *   ALL      — show every widget (default)
 *   SYSTEM   — show only modules with room === "system"
 *   <other>  — show modules whose room matches this string
 *  We surface "All" first, "System" last, and any other discovered rooms in
 *  between, alphabetically. Devices' room metadata isn't queried here — for
 *  Phase 1 the tabs are derived purely from module manifests; device-level
 *  room filtering arrives in Phase 3 when sensor widgets land. */
export const ALL_ROOM = '__all__';
export const SYSTEM_ROOM = 'system';

export interface RoomTabsProps {
  modules: Module[];
  active: string;
  onChange: (room: string) => void;
}

export function deriveRooms(modules: Module[]): string[] {
  const seen = new Set<string>();
  for (const m of modules) {
    if (m.room) seen.add(m.room);
  }
  const others = [...seen].filter((r) => r !== SYSTEM_ROOM).sort();
  const rooms: string[] = [ALL_ROOM, ...others];
  if (seen.has(SYSTEM_ROOM)) rooms.push(SYSTEM_ROOM);
  return rooms;
}

/** True when the module should appear under the given room tab. */
export function moduleMatchesRoom(mod: Module, room: string): boolean {
  if (room === ALL_ROOM) return true;
  return mod.room === room;
}

export default function RoomTabs({ modules, active, onChange }: RoomTabsProps) {
  const { t } = useTranslation();
  const rooms = useMemo(() => deriveRooms(modules), [modules]);

  // Hide the strip when only "All" is left — no value in showing a single tab.
  if (rooms.length <= 1) return null;

  return (
    <div
      role="tablist"
      style={{
        display: 'flex',
        gap: 6,
        padding: '0 4px 10px',
        overflowX: 'auto',
        scrollbarWidth: 'none',
      }}
    >
      {rooms.map((r) => {
        const isActive = r === active;
        return (
          <button
            key={r}
            role="tab"
            aria-selected={isActive}
            onClick={() => onChange(r)}
            style={{
              flex: '0 0 auto',
              padding: '4px 12px',
              borderRadius: 999,
              fontSize: 11,
              fontWeight: 500,
              cursor: 'pointer',
              background: isActive ? 'var(--ac)' : 'var(--sf)',
              color: isActive ? 'var(--on-accent)' : 'var(--tx2)',
              border: `1px solid ${isActive ? 'var(--ac)' : 'var(--b)'}`,
              transition: 'background .12s, color .12s, border-color .12s',
              whiteSpace: 'nowrap',
            }}
          >
            {labelFor(r, t)}
          </button>
        );
      })}
    </div>
  );
}

function labelFor(room: string, t: (k: string) => string): string {
  if (room === ALL_ROOM) return t('dashboardV2.rooms.all');
  if (room === SYSTEM_ROOM) return t('dashboardV2.rooms.system');
  // Try a translation key first; fall back to a Title-Cased version of the raw value.
  const candidate = t(`dashboardV2.rooms.${room}`);
  if (candidate && candidate !== `dashboardV2.rooms.${room}`) return candidate;
  return room.charAt(0).toUpperCase() + room.slice(1);
}
