import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import enManual from './locales/en';

const SAVED_LANG_KEY = 'selena-lang';
const savedLang = localStorage.getItem(SAVED_LANG_KEY) || 'en';

// EN is always eager — it's the fallback and covers the most common case.
// Every other language is loaded on demand via dynamic import; Vite code-splits
// each into its own chunk. 3-tier resolution: manual > community > auto.
// EN is statically imported above; exclude it from the lazy glob to keep Vite
// from producing a duplicate chunk plus the "dynamic + static import" warning.
const manualLoaders = import.meta.glob<{ translation?: Record<string, unknown> } | Record<string, unknown>>(
    ['./locales/*.ts', '!./locales/en.ts'],
    { import: 'default' }
);
const communityLoaders = import.meta.glob<Record<string, unknown>>(
    './locales/*.community.json',
    { import: 'default' }
);
const autoLoaders = import.meta.glob<Record<string, unknown>>(
    './locales/auto/*.auto.json',
    { import: 'default' }
);

const loadedLangs = new Set<string>(['en']);

function extractTranslation(mod: unknown): Record<string, unknown> {
    if (mod && typeof mod === 'object' && 'translation' in mod) {
        const wrapped = (mod as { translation?: unknown }).translation;
        if (wrapped && typeof wrapped === 'object') {
            return wrapped as Record<string, unknown>;
        }
    }
    return (mod ?? {}) as Record<string, unknown>;
}

async function loadLanguage(lang: string): Promise<void> {
    if (loadedLangs.has(lang)) return;

    const bundle: Record<string, unknown> = {};

    // Tier 3: auto (lowest priority, written first so higher tiers can override)
    const autoKey = `./locales/auto/${lang}.auto.json`;
    if (autoLoaders[autoKey]) {
        try {
            Object.assign(bundle, await autoLoaders[autoKey]());
        } catch (e) {
            console.warn(`[i18n] failed to load auto locale for ${lang}:`, e);
        }
    }

    // Tier 2: community overrides
    const communityKey = `./locales/${lang}.community.json`;
    if (communityLoaders[communityKey]) {
        try {
            Object.assign(bundle, await communityLoaders[communityKey]());
        } catch (e) {
            console.warn(`[i18n] failed to load community locale for ${lang}:`, e);
        }
    }

    // Tier 1: manual (highest priority)
    const manualKey = `./locales/${lang}.ts`;
    if (manualLoaders[manualKey]) {
        try {
            const mod = await manualLoaders[manualKey]();
            Object.assign(bundle, extractTranslation(mod));
        } catch (e) {
            console.warn(`[i18n] failed to load manual locale for ${lang}:`, e);
        }
    }

    i18n.addResourceBundle(lang, 'translation', bundle, true, true);
    loadedLangs.add(lang);
}

i18n.use(initReactI18next).init({
    resources: { en: enManual },
    lng: 'en',
    fallbackLng: 'en',
    interpolation: { escapeValue: false },
});

// If the user previously selected something other than EN, load it in the
// background and switch once ready. Initial render paints in EN — acceptable
// trade-off because it prevents a blank screen while the chunk streams in.
if (savedLang !== 'en') {
    loadLanguage(savedLang)
        .then(() => i18n.changeLanguage(savedLang))
        .catch((e) => console.warn(`[i18n] failed to restore saved lang ${savedLang}:`, e));
}

export async function changeLanguage(lang: string): Promise<void> {
    await loadLanguage(lang);
    await i18n.changeLanguage(lang);
    localStorage.setItem(SAVED_LANG_KEY, lang);
}

export function isLanguageLoaded(lang: string): boolean {
    return loadedLangs.has(lang);
}

export default i18n;
