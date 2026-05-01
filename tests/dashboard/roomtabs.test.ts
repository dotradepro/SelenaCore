/**
 * Tests for `RoomTabs.utils` — pure logic for the dashboard's room-tab strip.
 *
 * Run with:
 *     npm test                      # one-off
 *     npm run test:watch            # re-run on file changes
 */
import { test } from 'vitest';
import assert from 'node:assert/strict';
import {
  ALL_ROOM,
  SYSTEM_ROOM,
  deriveRooms,
  deviceRoom,
  moduleMatchesRoom,
  normalizeRoom,
} from '../../src/components/dashboard/RoomTabs.utils';
import type { Device, Module } from '../../src/store/useStore';

// ── Test data builders ──────────────────────────────────────────────────

function mod(name: string, room?: string): Module {
  return {
    name,
    version: '1.0.0',
    type: 'SYSTEM',
    status: 'RUNNING',
    runtime_mode: 'always_on',
    port: 0,
    installed_at: 0,
    room,
  } as Module;
}

function dev(opts: {
  id?: string;
  module_id?: string | null;
  location?: string | null;
  room?: string | null;
}): Device {
  return {
    device_id: opts.id ?? 'd-1',
    name: 'Device',
    type: 'light',
    protocol: 'fake',
    state: {},
    capabilities: [],
    last_seen: null,
    module_id: opts.module_id ?? null,
    meta: {},
    entity_type: null,
    location: opts.location ?? null,
    room: opts.room ?? null,
  } as Device;
}

// ── normalizeRoom ───────────────────────────────────────────────────────

test('normalizeRoom: trims whitespace and returns null for blank', () => {
  assert.equal(normalizeRoom('  Living  '), 'Living');
  assert.equal(normalizeRoom(''), null);
  assert.equal(normalizeRoom('   '), null);
  assert.equal(normalizeRoom(null), null);
  assert.equal(normalizeRoom(undefined), null);
});

// ── deriveRooms ─────────────────────────────────────────────────────────

test('deriveRooms: empty inputs → just ALL', () => {
  assert.deepEqual(deriveRooms([], []), [ALL_ROOM]);
});

test('deriveRooms: union of module.room and device.location, sorted, system last', () => {
  const modules = [
    mod('lights-switches', 'home'),
    mod('device-watchdog', 'system'),
  ];
  const devices = [
    dev({ id: 'd1', location: 'kitchen' }),
    dev({ id: 'd2', location: 'bedroom' }),
    dev({ id: 'd3', location: 'kitchen' }), // dedupe
  ];
  // Expect ALL first, "system" last, others alphabetical
  assert.deepEqual(deriveRooms(modules, devices), [
    ALL_ROOM,
    'bedroom',
    'home',
    'kitchen',
    SYSTEM_ROOM,
  ]);
});

test('deriveRooms: skips blank/null device locations', () => {
  const devices = [
    dev({ id: 'd1', location: 'kitchen' }),
    dev({ id: 'd2', location: null }),
    dev({ id: 'd3', location: '   ' }),
  ];
  assert.deepEqual(deriveRooms([], devices), [ALL_ROOM, 'kitchen']);
});

test('deriveRooms: SYSTEM only added when present', () => {
  // No system module/device → no SYSTEM tab
  const r = deriveRooms([mod('m', 'home')], [dev({ location: 'kitchen' })]);
  assert.equal(r.includes(SYSTEM_ROOM), false);
});

// ── moduleMatchesRoom ────────────────────────────────────────────────────

test('moduleMatchesRoom: ALL matches everything', () => {
  const m = mod('x', 'home');
  assert.equal(moduleMatchesRoom(m, ALL_ROOM, []), true);
});

test('moduleMatchesRoom: matches by module.room', () => {
  const m = mod('x', 'kitchen');
  assert.equal(moduleMatchesRoom(m, 'kitchen', []), true);
  assert.equal(moduleMatchesRoom(m, 'bedroom', []), false);
});

test('moduleMatchesRoom: matches via owned device location', () => {
  const m = mod('lights-switches', 'home');
  const devices = [
    dev({ id: 'd1', module_id: 'lights-switches', location: 'kitchen' }),
    dev({ id: 'd2', module_id: 'other-module', location: 'bedroom' }),
  ];
  // Same module owns a kitchen device → matches "kitchen"
  assert.equal(moduleMatchesRoom(m, 'kitchen', devices), true);
  // No device of this module is in bedroom → does not match
  assert.equal(moduleMatchesRoom(m, 'bedroom', devices), false);
});

test('moduleMatchesRoom: ignores devices owned by other modules', () => {
  const m = mod('foo', 'home');
  const devices = [
    dev({ id: 'd1', module_id: 'bar', location: 'kitchen' }),
  ];
  assert.equal(moduleMatchesRoom(m, 'kitchen', devices), false);
});

test('moduleMatchesRoom: device-control system module surfaces under any device room', () => {
  // The exact behaviour the user asked for: device-control declares
  // room: "system" but should still appear under any room where it owns
  // devices, because RoomTabs filtering folds device.location in.
  const dc = mod('device-control', 'system');
  const devices = [
    dev({ id: 'd1', module_id: 'device-control', location: 'living' }),
  ];
  assert.equal(moduleMatchesRoom(dc, 'living', devices), true);
  assert.equal(moduleMatchesRoom(dc, SYSTEM_ROOM, devices), true);
  assert.equal(moduleMatchesRoom(dc, 'kitchen', devices), false);
});

// ── Device.room ↔ Device.location alias ─────────────────────────────────

test('deviceRoom: prefers room when set, falls back to location', () => {
  // Both fields populated → room wins.
  assert.equal(deviceRoom(dev({ room: 'kitchen', location: 'old-name' })), 'kitchen');
  // Only location → falls back.
  assert.equal(deviceRoom(dev({ location: 'bedroom' })), 'bedroom');
  // Neither → null.
  assert.equal(deviceRoom(dev({})), null);
  // Whitespace coerced.
  assert.equal(deviceRoom(dev({ room: '  living  ' })), 'living');
});

test('deriveRooms: device.room is honored when set (alias of location)', () => {
  // Some devices have `room` set (post-normalization), some only `location`.
  // Both should contribute to the tab list identically.
  const devices = [
    dev({ id: 'd1', room: 'kitchen' }),
    dev({ id: 'd2', location: 'bedroom' }),
  ];
  assert.deepEqual(deriveRooms([], devices), [ALL_ROOM, 'bedroom', 'kitchen']);
});

test('moduleMatchesRoom: presence-detection hides under specific room (no devices owned)', () => {
  // presence-detection is whole-house: it tracks users, not Device rows.
  // It declares room: "system" and registers no Device entries with
  // module_id="presence-detection". So under a specific room tab
  // (e.g. "kitchen") the widget should NOT appear — only the System tab
  // and ALL surface it. This is the intentional design: per-room
  // presence info doesn't exist, so the global widget shouldn't pretend
  // to scope to a room.
  const presence = mod('presence-detection', 'system');
  const devices = [
    // Other modules' devices live in the kitchen, but none are owned by
    // presence-detection.
    dev({ id: 'd1', module_id: 'lights-switches', location: 'kitchen' }),
  ];
  assert.equal(moduleMatchesRoom(presence, 'kitchen', devices), false);
  assert.equal(moduleMatchesRoom(presence, SYSTEM_ROOM, devices), true);
  assert.equal(moduleMatchesRoom(presence, ALL_ROOM, devices), true);
});
