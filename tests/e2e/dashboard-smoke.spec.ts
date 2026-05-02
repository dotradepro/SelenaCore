import { test, expect } from '@playwright/test';

/** Read-only smoke for the dashboard. Assumes selena-core is healthy
 *  on http://localhost:80 (the merged UI proxy). Tests intentionally
 *  avoid mutating state — they verify the page renders, key API
 *  contracts respond, and obvious regressions don't sneak through.
 *
 *  Skips entirely if the backend is unreachable so the suite stays
 *  green on machines that don't have the container running. */
async function backendReachable(request: import('@playwright/test').APIRequestContext): Promise<boolean> {
  try {
    const r = await request.get('/api/ui/system', { timeout: 3_000 });
    return r.ok();
  } catch {
    return false;
  }
}

test.describe('dashboard smoke', () => {
  test.beforeAll(async ({ request }) => {
    const up = await backendReachable(request);
    test.skip(!up, 'selena-core not reachable on http://localhost — skipping live smoke');
  });

  test('root HTML loads with title', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveTitle(/Selena/i);
  });

  test('dashboard mounts and renders the hero greeting', async ({ page }) => {
    await page.goto('/');
    // Greeting is one of: "Good morning, X" / "Hello, X" / "Good evening, X"
    // (en) or the Ukrainian equivalent. The pattern matches any of them.
    const greeting = page.getByText(
      /(Good (morning|afternoon|evening|day)|Hello|Доброго (ранку|дня|вечора)|Привіт)/i,
    );
    await expect(greeting.first()).toBeVisible({ timeout: 8_000 });
  });

  test('module list endpoint returns RUNNING modules', async ({ request }) => {
    const r = await request.get('/api/ui/modules');
    expect(r.ok()).toBe(true);
    const body = await r.json();
    expect(Array.isArray(body.modules)).toBe(true);
    const running = body.modules.filter((m: { status: string }) => m.status === 'RUNNING');
    // A healthy container always has at least core system modules running.
    expect(running.length).toBeGreaterThan(0);
  });

  test('widget endpoints emit i18n label_key fields', async ({ request }) => {
    // Spot-check three widgets with known keys; if any one regresses
    // (e.g. a backend mod drops the `_key` companion), this fails.
    const checks: Array<{ path: string; expectedKey: string }> = [
      { path: '/api/v1/modules/lights-switches/data/state', expectedKey: 'widgets.lightsSwitches.label' },
      { path: '/api/v1/modules/device-watchdog/data/state',  expectedKey: 'widgets.deviceWatchdog.label' },
      { path: '/api/v1/modules/voice-core/data/state',       expectedKey: 'widgets.voiceCore.label' },
    ];
    for (const { path, expectedKey } of checks) {
      const r = await request.get(path);
      // Some modules return 409 in test envs (kind=custom etc.) — that's
      // not a failure of the i18n contract, just unwired widgets.
      if (r.status() === 409) continue;
      expect(r.ok(), `${path} not OK`).toBe(true);
      const body = await r.json();
      expect(body.label_key, `${path} missing label_key`).toBe(expectedKey);
    }
  });

  test('lights-switches widget includes per-item location field', async ({ request }) => {
    const r = await request.get('/api/v1/modules/lights-switches/data/state');
    if (r.status() === 409) test.skip();
    expect(r.ok()).toBe(true);
    const body = await r.json();
    expect(Array.isArray(body.items)).toBe(true);
    if (body.items.length > 0) {
      // The room-filter feature requires the field to exist on each item
      // (value can be null — that means "always pass through").
      expect(body.items[0]).toHaveProperty('location');
    }
  });

  test('climate widget exposes carousel-shape rooms when multiple devices exist', async ({ request }) => {
    const r = await request.get('/api/v1/modules/climate/data/state');
    if (r.status() === 503 || r.status() === 409) test.skip();
    expect(r.ok()).toBe(true);
    const body = await r.json();
    // With one device, rooms is absent (back-compat); with 2+, it lists
    // every room with primary first. Either is valid — just assert the
    // top-level still has a label/primary so the template can render.
    expect(body).toHaveProperty('label');
    expect(body).toHaveProperty('primary');
    if (Array.isArray(body.rooms)) {
      expect(body.rooms.length).toBeGreaterThanOrEqual(2);
      expect(body.rooms[0]).toHaveProperty('device_id');
    }
  });
});
