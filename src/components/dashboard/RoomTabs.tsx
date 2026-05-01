import { useTranslation } from 'react-i18next';
import { useMemo } from 'react';
import type { Device, Module } from '../../store/useStore';
import { ALL_ROOM, SYSTEM_ROOM, deriveRooms } from './RoomTabs.utils';

// Re-export pure helpers so existing import sites (DashboardV2, ToggleList)
// keep working without churn. The implementations live in RoomTabs.utils.ts
// to make them testable without React imports.
export { ALL_ROOM, SYSTEM_ROOM, deriveRooms, moduleMatchesRoom } from './RoomTabs.utils';

export interface RoomTabsProps {
  modules: Module[];
  devices: Device[];
  active: string;
  onChange: (room: string) => void;
}

export default function RoomTabs({ modules, devices, active, onChange }: RoomTabsProps) {
  const { t } = useTranslation();
  const rooms = useMemo(() => deriveRooms(modules, devices), [modules, devices]);

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
