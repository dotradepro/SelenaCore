import { defineConfig, devices } from '@playwright/test';

/** End-to-end smoke tests for the SelenaCore dashboard.
 *
 *  Targets the live containerised core at http://localhost (port 80 —
 *  the merged UI proxy). Tests are read-only checks of the dashboard
 *  surface: bundle loads, key API endpoints respond, room tabs +
 *  widgets render. Heavier interactive flows belong in vitest unit
 *  tests where they can run without a live backend.
 *
 *  Run with:
 *    npm run e2e
 *
 *  The container must already be healthy — the smoke does not spawn
 *  selena-core itself because production rebuilds happen via
 *  ``docker compose`` and aren't a test responsibility.
 */
export default defineConfig({
  testDir: './tests/e2e',
  timeout: 30_000,
  fullyParallel: false,
  retries: 0,
  workers: 1,
  reporter: 'list',
  use: {
    baseURL: 'http://localhost',
    headless: true,
    // Kiosk-sized viewport so smoke matches the deploy target.
    viewport: { width: 1080, height: 720 },
    actionTimeout: 5_000,
    navigationTimeout: 10_000,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
});
