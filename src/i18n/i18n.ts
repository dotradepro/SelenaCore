import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import en from './locales/en';
import uk from './locales/uk';

const savedLang = localStorage.getItem('selena-lang') || 'en';

i18n.use(initReactI18next).init({
    resources: { en, uk },
    lng: savedLang,
    fallbackLng: 'en',
    interpolation: { escapeValue: false },
});

export function changeLanguage(lang: string) {
    i18n.changeLanguage(lang);
    localStorage.setItem('selena-lang', lang);
}

export default i18n;
