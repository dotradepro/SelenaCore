import { defineConfig } from 'vitest/config';

/** Vitest config — keeps Playwright e2e tests under `tests/e2e/` out of
 *  the unit-test suite. Vitest's default include pattern picks up
 *  `*.spec.ts` files which Playwright also uses for its specs, so the
 *  exclude list is essential to prevent Vitest from trying to import
 *  ``@playwright/test`` (which doesn't run under Node alone).
 *
 *  Run unit tests with `npm test`; e2e with `npm run e2e`. */
export default defineConfig({
  test: {
    include: ['tests/**/*.test.ts'],
    exclude: ['tests/e2e/**', 'node_modules/**', 'dist/**'],
  },
});
