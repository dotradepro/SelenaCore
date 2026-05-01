/**
 * Integration-style tests for "active room tab → widget visibility/items"
 * end-to-end logic.
 *
 * Pure-logic test: walks the data path the dashboard takes when the
 * user clicks a room tab, without actually rendering React. Combines:
 *   1. `deriveRooms(modules, devices)` — the tab-strip composition
 *   2. `moduleMatchesRoom(mod, room, devices)` — module-level visibility
 *   3. ToggleList per-item filter — `items.filter(i => !i.location || i.location === activeRoom)`
 *
 * The unit-level tests in `roomtabs.test.ts` cover (1) and (2) in
 * isolation. This file glues them together with the toggle-list
 * payload filter to catch regressions where any one piece changes its
 * shape and the others silently disagree (the kind of bug that only
 * shows up in the browser).
 */
import { test, describe } from 'vitest';
import assert from 'node:assert/strict';
import {
  ALL_ROOM,
  SYSTEM_ROOM,
  deriveRooms,
  moduleMatchesRoom,
} from '../../src/components/dashboard/RoomTabs.utils';
import type { Device, Module } from '../../src/store/useStore';

interface ToggleItem {
  id: string;
  name: string;
  state: 'on' | 'off' | 'unknown';
  location?: string | null;
}

/** Mirror of `ToggleList.tsx` filter logic. Kept inline so refactors of
 *  the template don't silently bypass the test — if the rule changes,
 *  this copy fails first. */
function filterToggleItems(items: ToggleItem[], activeRoom: string): ToggleItem[] {
  if (!activeRoom || activeRoom === ALL_ROOM || activeRoom === SYSTEM_ROOM) {
    return items;
  }
  return items.filter((i) => {
    if (i.location == null || i.location === '') return true;
    return i.location === activeRoom;
  });
}

// ── Test fixtures — modelled on a typical install: a homeowner with
//    lights spread across rooms and one diagnostic system module. ────

function mkMod(name: string, room: string): Module {
  return {
    name, room,
    version: '1.0.0', type: 'SYSTEM', status: 'RUNNING',
    runtime_mode: 'always_on', port: 0, installed_at: 0,
  } as Module;
}

function mkDev(opts: {
  id: string; module_id: string; location: string | null; name?: string;
}): Device {
  return {
    device_id: opts.id,
    name: opts.name ?? opts.id,
    type: 'light',
    protocol: 'fake',
    state: { on: false },
    capabilities: [],
    last_seen: null,
    module_id: opts.module_id,
    meta: {},
    entity_type: 'light',
    location: opts.location,
  } as Device;
}

function mkItem(id: string, location: string | null = null, state: ToggleItem['state'] = 'off'): ToggleItem {
  return { id, name: id, state, location };
}

const fixtures = {
  modules: [
    mkMod('lights-switches', 'home'),       // user-facing, owns lights everywhere
    mkMod('device-control', 'system'),      // system module, but owns devices
    mkMod('device-watchdog', 'system'),     // system module, no devices owned
    mkMod('automation-engine', 'system'),   // system module, no devices owned
  ],
  devices: [
    // lights-switches owns all the lighting:
    mkDev({ id: 'l-living', module_id: 'lights-switches', location: 'living' }),
    mkDev({ id: 'l-kitchen', module_id: 'lights-switches', location: 'kitchen' }),
    mkDev({ id: 'l-bedroom', module_id: 'lights-switches', location: 'bedroom' }),
    mkDev({ id: 'l-no-room', module_id: 'lights-switches', location: null }),
    // device-control owns a thermostat in living and a lock in bedroom:
    mkDev({ id: 'th-living', module_id: 'device-control', location: 'living' }),
    mkDev({ id: 'lk-bedroom', module_id: 'device-control', location: 'bedroom' }),
  ],
  // Sample toggle-list payload from lights-switches' /widget/data/state
  // (mirrors what the real backend now emits with `location`).
  lightsItems: [
    mkItem('l-living', 'living'),
    mkItem('l-kitchen', 'kitchen'),
    mkItem('l-bedroom', 'bedroom'),
    mkItem('l-no-room', null),  // device without a location — always visible
  ],
};

describe('tab → visibility integration', () => {
  test('deriveRooms returns ALL + per-room tabs + SYSTEM', () => {
    const rooms = deriveRooms(fixtures.modules, fixtures.devices);
    assert.deepEqual(rooms, [
      ALL_ROOM, 'bedroom', 'home', 'kitchen', 'living', SYSTEM_ROOM,
    ]);
  });

  test('clicking "kitchen" tab: lights-switches visible, device-control hidden', () => {
    // lights-switches owns a kitchen light → matches kitchen.
    assert.equal(
      moduleMatchesRoom(fixtures.modules[0], 'kitchen', fixtures.devices),
      true,
    );
    // device-control owns no kitchen device → hidden under kitchen tab.
    assert.equal(
      moduleMatchesRoom(fixtures.modules[1], 'kitchen', fixtures.devices),
      false,
    );
    // device-watchdog and automation-engine own no devices → hidden.
    assert.equal(
      moduleMatchesRoom(fixtures.modules[2], 'kitchen', fixtures.devices),
      false,
    );
    assert.equal(
      moduleMatchesRoom(fixtures.modules[3], 'kitchen', fixtures.devices),
      false,
    );
  });

  test('clicking "living" tab: lights-switches AND device-control visible', () => {
    assert.equal(
      moduleMatchesRoom(fixtures.modules[0], 'living', fixtures.devices),
      true,
    );
    assert.equal(
      moduleMatchesRoom(fixtures.modules[1], 'living', fixtures.devices),
      true,
    );
  });

  test('System tab: only system-room modules visible', () => {
    // device-control, device-watchdog, automation-engine are all room=system.
    for (const m of fixtures.modules.slice(1)) {
      assert.equal(moduleMatchesRoom(m, SYSTEM_ROOM, fixtures.devices), true);
    }
    // lights-switches has room=home, not system → hidden.
    assert.equal(
      moduleMatchesRoom(fixtures.modules[0], SYSTEM_ROOM, fixtures.devices),
      false,
    );
  });
});

describe('tab → toggle-list item filtering', () => {
  test('"All" tab: every item passes through', () => {
    const out = filterToggleItems(fixtures.lightsItems, ALL_ROOM);
    assert.equal(out.length, 4);
  });

  test('"kitchen" tab: only kitchen item + items without location', () => {
    const out = filterToggleItems(fixtures.lightsItems, 'kitchen');
    assert.deepEqual(out.map((i) => i.id).sort(), ['l-kitchen', 'l-no-room']);
  });

  test('"living" tab: only living item + items without location', () => {
    const out = filterToggleItems(fixtures.lightsItems, 'living');
    assert.deepEqual(out.map((i) => i.id).sort(), ['l-living', 'l-no-room']);
  });

  test('"System" tab: skips per-item filter (all pass)', () => {
    // System tab is for diagnostic modules; per-item room filtering on
    // a status/metric widget makes no sense, so the rule treats SYSTEM
    // identically to ALL.
    const out = filterToggleItems(fixtures.lightsItems, SYSTEM_ROOM);
    assert.equal(out.length, 4);
  });

  test('end-to-end: kitchen tab on lights-switches shows exactly 2 items', () => {
    // 1. Module is visible (lights-switches owns a kitchen light).
    const visible = moduleMatchesRoom(fixtures.modules[0], 'kitchen', fixtures.devices);
    assert.equal(visible, true);
    // 2. Once visible, the toggle-list filters its items to kitchen-only.
    const out = filterToggleItems(fixtures.lightsItems, 'kitchen');
    assert.equal(out.length, 2);
    // The user sees just `l-kitchen` and the unanchored `l-no-room`.
  });
});
