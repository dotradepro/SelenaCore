import type { Device, Module } from '../../store/useStore';

/** Sentinel room values used by the dashboard:
 *   ALL      — show every widget (default)
 *   SYSTEM   — show only modules with room === "system"
 *   <other>  — show modules whose room matches this string, OR modules
 *             that own at least one device with location === that string
 *  Surface "All" first, "System" last, alphabetical in between. */
export const ALL_ROOM = '__all__';
export const SYSTEM_ROOM = 'system';

/** Trim/empty-coerce a room string. Returns ``null`` for absent/blank. */
export function normalizeRoom(value: string | null | undefined): string | null {
  const trimmed = (value ?? '').trim();
  return trimmed.length > 0 ? trimmed : null;
}

/** Read the device's room. Prefers the unified `room` field (set by the
 *  store) and falls back to backend-canonical `location` when the store
 *  hasn't normalized yet (e.g. a payload that bypassed the normalizer). */
export function deviceRoom(d: Device): string | null {
  return normalizeRoom(d.room) ?? normalizeRoom(d.location);
}

/** Derive the ordered list of room tabs from the union of `module.room`
 *  and `device.room` (≡ `device.location`). Always starts with ALL_ROOM;
 *  SYSTEM_ROOM appears last when at least one module/device declares it. */
export function deriveRooms(modules: Module[], devices: Device[]): string[] {
  const seen = new Set<string>();
  for (const m of modules) {
    const r = normalizeRoom(m.room);
    if (r) seen.add(r);
  }
  for (const d of devices) {
    const r = deviceRoom(d);
    if (r) seen.add(r);
  }
  const others = [...seen].filter((r) => r !== SYSTEM_ROOM).sort();
  const rooms: string[] = [ALL_ROOM, ...others];
  if (seen.has(SYSTEM_ROOM)) rooms.push(SYSTEM_ROOM);
  return rooms;
}

/** True when the module should appear under the given room tab.
 *  A module matches a room if its manifest declares that room, or if it
 *  owns at least one device whose `room`/`location` matches. */
export function moduleMatchesRoom(
  mod: Module,
  room: string,
  devices: Device[],
): boolean {
  if (room === ALL_ROOM) return true;
  if (mod.room === room) return true;
  for (const d of devices) {
    if (d.module_id !== mod.name) continue;
    if (deviceRoom(d) === room) return true;
  }
  return false;
}
