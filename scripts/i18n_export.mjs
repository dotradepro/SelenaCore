#!/usr/bin/env node
// Exports a locale TS file as JSON to stdout.
//
// Usage:  npx tsx scripts/i18n_export.mjs <lang>
// Example: npx tsx scripts/i18n_export.mjs en
//
// The locale modules export either `{ translation: {...} }` (i18next shape)
// or the bare object. This helper unwraps `translation` so downstream tools
// see a flat key tree regardless.

import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const repoRoot = resolve(__dirname, '..');

const lang = process.argv[2];
if (!lang) {
    process.stderr.write('usage: i18n_export.mjs <lang>\n');
    process.exit(2);
}

const modPath = resolve(repoRoot, 'src/i18n/locales', `${lang}.ts`);

try {
    const mod = await import(modPath);
    const raw = mod.default ?? mod;
    const unwrapped = (raw && typeof raw === 'object' && 'translation' in raw)
        ? raw.translation
        : raw;
    process.stdout.write(JSON.stringify(unwrapped, null, 2));
} catch (err) {
    process.stderr.write(`failed to load ${lang}: ${err.message}\n`);
    process.exit(1);
}
