import { useState, useEffect, useCallback, useRef } from 'react';
import { Routes, Route, Link, useLocation, useNavigate, Navigate } from 'react-router-dom';
import { Volume2, Network, Users, Activity, RefreshCw, Play, Check, Wifi, Lock, Globe, Cpu, Palette, Plus, Trash2, Edit3, Smartphone, Bell, QrCode, Search, X, LayoutGrid } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { cn } from '../lib/utils';
import { useStore } from '../store/useStore';
import UsersPanel from './UsersPanel';
import Modules from './Modules';
import ModuleDetail from './ModuleDetail';
import SystemPage from './SystemPage';
import ProvisionProgress from './ProvisionProgress';
// Wrapper for tabs whose root content is just a form / list with no
// built-in scroll container.  Defined OUTSIDE the component so React
// preserves the component identity across re-renders (prevents
// unmount/remount of children which would re-fire useEffects).
function FormPage({ children }: { children: React.ReactNode }) {
  return <div className="generic-page">{children}</div>;
}

export default function Settings() {
  const { t } = useTranslation();
  const location = useLocation();

  const tabs = [
    { id: 'system-info', label: t('settings.systemInfo', 'System Info'), icon: Cpu, path: '/settings/system-info' },
    { id: 'appearance', label: t('settings.appearance'), icon: Palette, path: '/settings/appearance' },
    { id: 'audio', label: t('settings.audio'), icon: Volume2, path: '/settings/audio' },
    { id: 'network', label: t('settings.networkAndVpn'), icon: Network, path: '/settings/network' },
    { id: 'users', label: t('settings.users'), icon: Users, path: '/settings/users' },
    { id: 'modules', label: t('settings.modules', 'Modules'), icon: LayoutGrid, path: '/settings/modules' },
    { id: 'system', label: t('settings.system'), icon: Activity, path: '/settings/system' },
    { id: 'system-modules', label: t('settings.systemModules'), icon: Cpu, path: '/settings/system-modules' },
  ];

  const activeId =
    tabs.find(tab => location.pathname === tab.path || location.pathname.startsWith(tab.path + '/'))?.id ??
    (location.pathname === '/settings' ? 'system-info' : '');

  return (
    <div style={{ height: '100%', minHeight: 0, display: 'flex', flexDirection: 'column' }}>
      {/* Horizontal tab bar */}
      <div style={{ display: 'flex', borderBottom: '1px solid var(--b)', overflowX: 'auto', flexShrink: 0, scrollbarWidth: 'none' }}>
        {tabs.map((tab) => {
          const isActive = tab.id === activeId;
          return (
            <Link
              key={tab.id}
              to={tab.path}
              style={{
                display: 'flex', alignItems: 'center', gap: 6,
                padding: '10px 16px',
                fontSize: 12, fontWeight: isActive ? 600 : 400,
                color: isActive ? 'var(--tx)' : 'var(--tx3)',
                borderBottom: `2px solid ${isActive ? 'var(--ac)' : 'transparent'}`,
                whiteSpace: 'nowrap', cursor: 'pointer',
                transition: 'color .15s', textDecoration: 'none',
              }}
            >
              <tab.icon size={13} />
              {tab.label}
            </Link>
          );
        })}
      </div>

      {/* Settings content
          flex:1 + minHeight:0 makes this region take exactly the remaining
          viewport height. overflow:hidden delegates scrolling to the inner
          page (so we never get a double scrollbar). Pages that already
          manage their own scroll (Modules, ModuleDetail, SystemPage,
          IntegrityPage) render directly; simple form tabs are wrapped
          in <FormPage> which provides the .generic-page scroller. */}
      <div style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
        <Routes>
          <Route path="/"               element={<Navigate to="/settings/system-info" replace />} />
          <Route path="/appearance"     element={<FormPage><AppearanceSettings /></FormPage>} />
          <Route path="/audio"          element={<FormPage><AudioSettings /></FormPage>} />
          <Route path="/network"        element={<FormPage><NetworkSettings /></FormPage>} />
          <Route path="/users"          element={<FormPage><UsersSettings /></FormPage>} />
          <Route path="/modules"        element={<Modules />} />
          <Route path="/modules/:name"  element={<ModuleDetail />} />
          <Route path="/system"         element={<FormPage><SystemSettings /></FormPage>} />
          <Route path="/system-info"    element={<SystemPage />} />
          <Route path="/system-info/:tab" element={<SystemPage />} />
          <Route path="/system-modules" element={<SystemModulesSettings />} />
          <Route path="*" element={<FormPage><div className="text-zinc-400">{t('common.inDevelopment')}</div></FormPage>} />
        </Routes>
      </div>
    </div>
  );
}

// ================================================================ //
//  Appearance Settings                                                //
// ================================================================ //

import type { ThemeMode, CustomTheme, WallpaperInfo } from '../store/useStore';

/* ── Swatch row — shows 5 key colors from a theme variant ── */
function ThemeSwatches({ colors }: { colors: Record<string, string> }) {
  const keys = ['bg', 'ac', 'gr', 'am', 'rd'];
  // Resolve: if the theme has the key use it, otherwise fall back to computed CSS var
  const resolve = (k: string) => colors[k] || getComputedStyle(document.documentElement).getPropertyValue(`--${k}`).trim() || '#888';
  return (
    <div style={{ display: 'flex', gap: 4, marginTop: 6 }}>
      {keys.map(k => (
        <div key={k} style={{ width: 16, height: 16, borderRadius: '50%', background: resolve(k), border: '1px solid rgba(128,128,128,.3)' }} />
      ))}
    </div>
  );
}

/* ── Default CSS variable values (for "Copy from default" button) ── */
const DEFAULT_DARK: Record<string, string> = {
  bg: '#0B0C10', sf: '#14151E', sf2: '#1B1C27', sf3: '#242532',
  b: 'rgba(255,255,255,.12)', b2: 'rgba(255,255,255,.20)',
  tx: '#EDEEF5', tx2: '#A0A5BE', tx3: '#6B7194',
  ac: '#5A96FF', gr: '#34D693', am: '#F5A93A', rd: '#E05454', pu: '#9B6EF4', tl: '#2AB4C4',
};
const DEFAULT_LIGHT: Record<string, string> = {
  bg: '#EFF0F5', sf: '#FFFFFF', sf2: '#F4F5F9', sf3: '#E8E9F0',
  b: 'rgba(0,0,0,.13)', b2: 'rgba(0,0,0,.22)',
  tx: '#1A1C24', tx2: '#4A4F68', tx3: '#7C8198',
  ac: '#3B7AE8', gr: '#1FAF75', am: '#DB8F1C', rd: '#C94040', pu: '#7C52D8', tl: '#1E9AA8',
};

const VAR_GROUPS: { label: string; vars: string[] }[] = [
  { label: 'Backgrounds', vars: ['bg', 'sf', 'sf2', 'sf3'] },
  { label: 'Text', vars: ['tx', 'tx2', 'tx3'] },
  { label: 'Accents', vars: ['ac', 'gr', 'am', 'rd', 'pu', 'tl'] },
];

const VAR_LABELS: Record<string, string> = {
  bg: 'varBg', sf: 'varSf', sf2: 'varSf2', sf3: 'varSf3',
  b: 'varB', b2: 'varB2',
  tx: 'varTx', tx2: 'varTx2', tx3: 'varTx3',
  ac: 'varAc', gr: 'varGr', am: 'varAm', rd: 'varRd', pu: 'varPu', tl: 'varTl',
};

/* ── hex <-> input helpers ── */
function isHex(v: string) { return /^#[0-9a-fA-F]{3,8}$/.test(v); }

/* ── Theme Editor Modal ── */
function ThemeEditorModal({ theme, onSave, onClose }: {
  theme: CustomTheme | null; // null = create new
  onSave: (name: { en: string; uk: string }, dark: Record<string, string>, light: Record<string, string>) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [nameEn, setNameEn] = useState(theme?.name?.en || '');
  const [nameUk, setNameUk] = useState(theme?.name?.uk || '');
  const [dark, setDark] = useState<Record<string, string>>({ ...DEFAULT_DARK, ...(theme?.dark || {}) });
  const [light, setLight] = useState<Record<string, string>>({ ...DEFAULT_LIGHT, ...(theme?.light || {}) });

  const setVar = (variant: 'dark' | 'light', key: string, val: string) => {
    if (variant === 'dark') setDark(prev => ({ ...prev, [key]: val }));
    else setLight(prev => ({ ...prev, [key]: val }));
  };

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 9999,
      background: 'rgba(0,0,0,.6)', backdropFilter: 'blur(6px)', WebkitBackdropFilter: 'blur(6px)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16,
    }} onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div style={{
        background: 'var(--sf)', border: '1px solid var(--b)', borderRadius: 16, padding: 24,
        maxWidth: 680, width: '100%', maxHeight: '85vh', overflowY: 'auto', color: 'var(--tx)',
      }}>
        <h3 style={{ fontSize: 18, fontWeight: 600, marginBottom: 16 }}>
          {theme ? t('settings.editTheme') : t('settings.createTheme')}
        </h3>

        {/* Name inputs */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 20 }}>
          <div>
            <label style={{ fontSize: 12, color: 'var(--tx2)', marginBottom: 4, display: 'block' }}>{t('settings.themeNameEn')}</label>
            <input value={nameEn} onChange={e => setNameEn(e.target.value)} placeholder="My Theme"
              style={{ width: '100%', padding: '8px 12px', borderRadius: 8, border: '1px solid var(--b)', background: 'var(--sf2)', color: 'var(--tx)', fontSize: 13 }} />
          </div>
          <div>
            <label style={{ fontSize: 12, color: 'var(--tx2)', marginBottom: 4, display: 'block' }}>{t('settings.themeNameUk')}</label>
            <input value={nameUk} onChange={e => setNameUk(e.target.value)} placeholder="Моя тема"
              style={{ width: '100%', padding: '8px 12px', borderRadius: 8, border: '1px solid var(--b)', background: 'var(--sf2)', color: 'var(--tx)', fontSize: 13 }} />
          </div>
        </div>

        {/* Copy from default */}
        <button onClick={() => { setDark({ ...DEFAULT_DARK }); setLight({ ...DEFAULT_LIGHT }); }}
          style={{ padding: '6px 14px', borderRadius: 8, border: '1px solid var(--b)', background: 'var(--sf2)', color: 'var(--tx2)', cursor: 'pointer', fontSize: 12, marginBottom: 16 }}>
          {t('settings.copyFromDefault')}
        </button>

        {/* Color editor — two columns */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
          {(['dark', 'light'] as const).map(variant => (
            <div key={variant}>
              <h4 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12, color: 'var(--tx)' }}>
                {variant === 'dark' ? t('settings.darkVariant') : t('settings.lightVariant')}
              </h4>
              <ThemeSwatches colors={variant === 'dark' ? dark : light} />
              <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 6 }}>
                {VAR_GROUPS.map(group => (
                  <div key={group.label}>
                    <div style={{ fontSize: 11, color: 'var(--tx3)', marginTop: 8, marginBottom: 4, textTransform: 'uppercase', letterSpacing: 1 }}>{group.label}</div>
                    {group.vars.map(v => {
                      const val = (variant === 'dark' ? dark : light)[v] || '';
                      const hex = isHex(val);
                      return (
                        <div key={v} style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3 }}>
                          <span style={{ fontSize: 11, color: 'var(--tx2)', width: 70, flexShrink: 0 }}>{t(`settings.${VAR_LABELS[v]}`)}</span>
                          {hex && (
                            <input type="color" value={val.length === 4 ? `#${val[1]}${val[1]}${val[2]}${val[2]}${val[3]}${val[3]}` : val}
                              onChange={e => setVar(variant, v, e.target.value)}
                              style={{ width: 24, height: 24, border: 'none', background: 'none', cursor: 'pointer', padding: 0 }} />
                          )}
                          <input value={val} onChange={e => setVar(variant, v, e.target.value)}
                            style={{ flex: 1, padding: '4px 8px', borderRadius: 6, border: '1px solid var(--b)', background: 'var(--sf2)', color: 'var(--tx)', fontSize: 11, fontFamily: 'var(--font-mono, monospace)' }} />
                        </div>
                      );
                    })}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>

        {/* Actions */}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 20 }}>
          <button onClick={onClose}
            style={{ padding: '8px 20px', borderRadius: 8, border: '1px solid var(--b)', background: 'var(--sf2)', color: 'var(--tx)', cursor: 'pointer', fontSize: 13 }}>
            Cancel
          </button>
          <button onClick={() => {
            if (!nameEn.trim()) return;
            onSave({ en: nameEn.trim(), uk: nameUk.trim() || nameEn.trim() }, dark, light);
          }}
            style={{ padding: '8px 20px', borderRadius: 8, border: 'none', background: 'var(--ac)', color: '#fff', cursor: 'pointer', fontSize: 13, fontWeight: 600 }}>
            {theme ? t('settings.themeUpdated').replace(/\.$/, '') : t('settings.themeCreated').replace(/\.$/, '')}
          </button>
        </div>
      </div>
    </div>
  );
}

function AppearanceSettings() {
  const { t, i18n } = useTranslation();
  const lang = i18n.language as 'en' | 'uk';
  const theme = useStore(s => s.theme);
  const setTheme = useStore(s => s.setTheme);
  const selectedLanguage = useStore(s => s.selectedLanguage);
  const setSelectedLanguage = useStore(s => s.setSelectedLanguage);
  const showToast = useStore(s => s.showToast);

  // Custom themes
  const customThemes = useStore(s => s.customThemes);
  const activeThemeId = useStore(s => s.activeThemeId);
  const activateTheme = useStore(s => s.activateTheme);
  const createTheme = useStore(s => s.createTheme);
  const updateTheme = useStore(s => s.updateTheme);
  const deleteTheme = useStore(s => s.deleteTheme);

  // Wallpapers
  const wallpapers = useStore(s => s.wallpapers);
  const activeWallpaper = useStore(s => s.activeWallpaper);
  const wallpaperBlur = useStore(s => s.wallpaperBlur);
  const wallpaperOpacity = useStore(s => s.wallpaperOpacity);
  const setWallpaper = useStore(s => s.setWallpaper);

  const [editorOpen, setEditorOpen] = useState(false);
  const [editingTheme, setEditingTheme] = useState<CustomTheme | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const themeOptions: { value: ThemeMode; label: string; desc: string; icon: string }[] = [
    { value: 'auto', label: t('settings.themeAuto'), desc: t('settings.themeAutoDesc'), icon: '🖥️' },
    { value: 'dark', label: t('settings.themeDark'), desc: t('settings.themeDarkDesc'), icon: '🌙' },
    { value: 'light', label: t('settings.themeLight'), desc: t('settings.themeLightDesc'), icon: '☀️' },
  ];

  const languages = [
    { code: 'en', label: 'English', flag: '🇬🇧' },
    { code: 'uk', label: 'Українська', flag: '🇺🇦' },
  ];

  const themeName = (th: CustomTheme) => th.name?.[lang] || th.name?.en || th.id;

  return (
    <div className="space-y-8">
      <div>
        <h3 style={{ fontSize: 20, fontWeight: 600, marginBottom: 4, color: 'var(--tx)' }}>{t('settings.appearance')}</h3>
        <p style={{ fontSize: 13, color: 'var(--tx2)' }}>{t('settings.appearanceDesc')}</p>
      </div>

      {/* ═══ Color Theme picker ═══ */}
      <div style={{ background: 'var(--sf)', border: '1px solid var(--b)', borderRadius: 12, padding: 20 }}>
        <h4 style={{ fontWeight: 500, marginBottom: 4, color: 'var(--tx)' }}>{t('settings.colorTheme')}</h4>
        <p style={{ fontSize: 12, color: 'var(--tx3)', marginBottom: 14 }}>{t('settings.colorThemeDesc')}</p>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
          {customThemes.map(th => {
            const isActive = activeThemeId === th.id;
            return (
              <div key={th.id} style={{ position: 'relative' }}>
                <button
                  onClick={() => { activateTheme(th.id); showToast(t('settings.themeActivated')); }}
                  style={{
                    padding: '12px 16px', borderRadius: 10,
                    border: `2px solid ${isActive ? 'var(--ac)' : 'var(--b)'}`,
                    background: isActive ? 'rgba(79,140,247,.08)' : 'var(--sf2)',
                    cursor: 'pointer', minWidth: 100, textAlign: 'center', transition: 'all .15s',
                  }}
                >
                  <span style={{ fontSize: 13, fontWeight: 600, color: isActive ? 'var(--ac)' : 'var(--tx)', display: 'block' }}>
                    {themeName(th)}
                  </span>
                  <ThemeSwatches colors={th.dark} />
                </button>
                {/* Edit / Delete for custom (non-builtIn) themes */}
                {!th.builtIn && (
                  <div style={{ display: 'flex', gap: 4, marginTop: 4, justifyContent: 'center' }}>
                    <button onClick={() => { setEditingTheme(th); setEditorOpen(true); }}
                      style={{ fontSize: 11, color: 'var(--tx3)', background: 'none', border: 'none', cursor: 'pointer', textDecoration: 'underline' }}>
                      {t('settings.editTheme')}
                    </button>
                    <button onClick={() => {
                      if (confirm(t('settings.deleteThemeConfirm'))) {
                        deleteTheme(th.id);
                        showToast(t('settings.themeDeleted'));
                      }
                    }}
                      style={{ fontSize: 11, color: 'var(--rd)', background: 'none', border: 'none', cursor: 'pointer', textDecoration: 'underline' }}>
                      {t('settings.deleteTheme')}
                    </button>
                  </div>
                )}
              </div>
            );
          })}
          {/* + Create button */}
          <button
            onClick={() => { setEditingTheme(null); setEditorOpen(true); }}
            style={{
              padding: '12px 16px', borderRadius: 10,
              border: '2px dashed var(--b)', background: 'none',
              cursor: 'pointer', minWidth: 80, display: 'flex', flexDirection: 'column',
              alignItems: 'center', justifyContent: 'center', gap: 4, transition: 'all .15s',
            }}
          >
            <span style={{ fontSize: 22, color: 'var(--tx3)' }}>+</span>
            <span style={{ fontSize: 11, color: 'var(--tx3)' }}>{t('settings.createTheme')}</span>
          </button>
        </div>
      </div>

      {/* ═══ Wallpaper picker ═══ */}
      <div style={{ background: 'var(--sf)', border: '1px solid var(--b)', borderRadius: 12, padding: 20 }}>
        <h4 style={{ fontWeight: 500, marginBottom: 4, color: 'var(--tx)' }}>{t('settings.wallpaper')}</h4>
        <p style={{ fontSize: 12, color: 'var(--tx3)', marginBottom: 14 }}>{t('settings.wallpaperDesc')}</p>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(100px, 1fr))', gap: 8, marginBottom: 14 }}>
          {/* None option */}
          <button
            onClick={() => setWallpaper(null)}
            style={{
              aspectRatio: '16/9', borderRadius: 8,
              border: `2px solid ${!activeWallpaper ? 'var(--ac)' : 'var(--b)'}`,
              background: !activeWallpaper ? 'rgba(79,140,247,.08)' : 'var(--sf2)',
              cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 11, color: 'var(--tx3)', transition: 'all .15s',
            }}
          >
            {t('settings.wallpaperNone')}
          </button>
          {/* Wallpaper thumbnails */}
          {wallpapers.map((wp: WallpaperInfo) => {
            const isActive = activeWallpaper === wp.filename;
            return (
              <button
                key={wp.id}
                onClick={() => setWallpaper(wp.filename, wallpaperBlur, wallpaperOpacity)}
                style={{
                  aspectRatio: '16/9', borderRadius: 8,
                  border: `2px solid ${isActive ? 'var(--ac)' : 'var(--b)'}`,
                  background: `url(${wp.url}) center/cover no-repeat`,
                  cursor: 'pointer', transition: 'all .15s',
                }}
                title={wp.filename}
              />
            );
          })}
        </div>
        {/* Opacity + Blur sliders */}
        {activeWallpaper && (
          <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
            <div style={{ flex: 1, minWidth: 140 }}>
              <label style={{ fontSize: 12, color: 'var(--tx2)', display: 'block', marginBottom: 4 }}>
                {t('settings.wallpaperOpacity')}: {Math.round(wallpaperOpacity * 100)}%
              </label>
              <input type="range" min="5" max="50" value={Math.round(wallpaperOpacity * 100)}
                onChange={e => {
                  const val = Number(e.target.value) / 100;
                  useStore.setState({ wallpaperOpacity: val });
                  if (debounceRef.current) clearTimeout(debounceRef.current);
                  debounceRef.current = setTimeout(() => setWallpaper(activeWallpaper, wallpaperBlur, val), 300);
                }}
                style={{ width: '100%', accentColor: 'var(--ac)' }} />
            </div>
            <div style={{ flex: 1, minWidth: 140 }}>
              <label style={{ fontSize: 12, color: 'var(--tx2)', display: 'block', marginBottom: 4 }}>
                {t('settings.wallpaperBlur')}: {wallpaperBlur}px
              </label>
              <input type="range" min="0" max="20" value={wallpaperBlur}
                onChange={e => {
                  const val = Number(e.target.value);
                  useStore.setState({ wallpaperBlur: val });
                  if (debounceRef.current) clearTimeout(debounceRef.current);
                  debounceRef.current = setTimeout(() => setWallpaper(activeWallpaper, val, wallpaperOpacity), 300);
                }}
                style={{ width: '100%', accentColor: 'var(--ac)' }} />
            </div>
          </div>
        )}
      </div>

      {/* ═══ Mode selector (dark/light/auto) ═══ */}
      <div style={{ background: 'var(--sf)', border: '1px solid var(--b)', borderRadius: 12, padding: 20 }}>
        <h4 style={{ fontWeight: 500, marginBottom: 16, color: 'var(--tx)' }}>{t('settings.mode')}</h4>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12 }}>
          {themeOptions.map(opt => {
            const isActive = theme === opt.value;
            return (
              <button
                key={opt.value}
                onClick={() => { setTheme(opt.value); showToast(t('settings.themeChanged')); }}
                style={{
                  padding: '16px 12px',
                  borderRadius: 10,
                  border: `2px solid ${isActive ? 'var(--ac)' : 'var(--b)'}`,
                  background: isActive ? 'rgba(79,140,247,.08)' : 'var(--sf2)',
                  cursor: 'pointer',
                  display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8,
                  transition: 'all .15s',
                }}
              >
                <span style={{ fontSize: 28 }}>{opt.icon}</span>
                <span style={{ fontSize: 13, fontWeight: 600, color: isActive ? 'var(--ac)' : 'var(--tx)' }}>{opt.label}</span>
                <span style={{ fontSize: 11, color: 'var(--tx3)', textAlign: 'center' }}>{opt.desc}</span>
              </button>
            );
          })}
        </div>
      </div>

      {/* ═══ Language selector ═══ */}
      <div style={{ background: 'var(--sf)', border: '1px solid var(--b)', borderRadius: 12, padding: 20 }}>
        <h4 style={{ fontWeight: 500, marginBottom: 16, color: 'var(--tx)' }}>{t('settings.language')}</h4>
        <div style={{ display: 'flex', gap: 12 }}>
          {languages.map(l => {
            const isActive = selectedLanguage === l.code;
            return (
              <button
                key={l.code}
                onClick={() => { setSelectedLanguage(l.code); showToast(t('settings.languageChanged')); }}
                style={{
                  padding: '12px 24px',
                  borderRadius: 10,
                  border: `2px solid ${isActive ? 'var(--ac)' : 'var(--b)'}`,
                  background: isActive ? 'rgba(79,140,247,.08)' : 'var(--sf2)',
                  cursor: 'pointer',
                  display: 'flex', alignItems: 'center', gap: 10,
                  transition: 'all .15s',
                }}
              >
                <span style={{ fontSize: 22 }}>{l.flag}</span>
                <span style={{ fontSize: 13, fontWeight: 500, color: isActive ? 'var(--ac)' : 'var(--tx)' }}>{l.label}</span>
              </button>
            );
          })}
        </div>
      </div>

      {/* ═══ Theme Editor Modal ═══ */}
      {editorOpen && (
        <ThemeEditorModal
          theme={editingTheme}
          onClose={() => { setEditorOpen(false); setEditingTheme(null); }}
          onSave={async (name, dark, light) => {
            if (editingTheme) {
              await updateTheme(editingTheme.id, name, dark, light);
              showToast(t('settings.themeUpdated'));
            } else {
              const created = await createTheme(name, dark, light);
              if (created) {
                showToast(t('settings.themeCreated'));
                activateTheme(created.id);
              }
            }
            setEditorOpen(false);
            setEditingTheme(null);
          }}
        />
      )}
    </div>
  );
}

// ================================================================ //
//  Audio Settings                                                     //
// ================================================================ //

function AudioSettings() {
  const { t } = useTranslation();
  const [inputs, setInputs] = useState<any[]>([]);
  const [outputs, setOutputs] = useState<any[]>([]);
  const [selectedInput, setSelectedInput] = useState('');
  const [selectedOutput, setSelectedOutput] = useState('');
  const [testingOutput, setTestingOutput] = useState(false);
  const [testingInput, setTestingInput] = useState(false);
  const [micLevel, setMicLevel] = useState<number | null>(null);
  const [countdown, setCountdown] = useState(0);
  const [playingBack, setPlayingBack] = useState(false);
  const [outputVolume, setOutputVolume] = useState(100);
  const [inputGain, setInputGain] = useState(100);
  const [liveMicLevel, setLiveMicLevel] = useState(0);
  const [micMonitoring, setMicMonitoring] = useState(false);
  const micMonitorRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [savingAudio, setSavingAudio] = useState(false);
  const showToast = useStore(s => s.showToast);

  useEffect(() => {
    fetch('/api/ui/setup/audio/devices').then(r => r.json()).then(data => {
      const ins = data.inputs || [];
      const outs = data.outputs || [];
      setInputs(ins);
      setOutputs(outs);
      setSelectedInput(data.selected_input || (ins.length ? ins[0].id : ''));
      setSelectedOutput(data.selected_output || (outs.length ? outs[0].id : ''));
    }).catch(() => { });
    fetch('/api/ui/setup/audio/levels').then(r => r.json()).then(data => {
      setOutputVolume(data.output_volume ?? 100);
      setInputGain(data.input_gain ?? 100);
    }).catch(() => { });
    return () => { if (micMonitorRef.current) clearInterval(micMonitorRef.current); };
  }, []);

  const saveAudio = async () => {
    setSavingAudio(true);
    try {
      await fetch('/api/ui/setup/audio/select', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ input: selectedInput, output: selectedOutput }),
      });
      await fetch('/api/ui/setup/audio/levels', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ output_volume: outputVolume, input_gain: inputGain }),
      });
      showToast(t('settings.audioSaved'));
    } catch {
      showToast(t('settings.audioSaveError'), 'error');
    } finally {
      setSavingAudio(false);
    }
  };

  const applyVolume = async (vol: number) => {
    setOutputVolume(vol);
    await fetch('/api/ui/setup/audio/levels', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ output_volume: vol }),
    }).catch(() => {});
  };

  const applyGain = async (gain: number) => {
    setInputGain(gain);
    await fetch('/api/ui/setup/audio/levels', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ input_gain: gain }),
    }).catch(() => {});
  };

  const testOutput = async () => {
    setTestingOutput(true);
    try {
      await fetch('/api/ui/setup/audio/test/output', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ device: selectedOutput }),
      });
      showToast(t('settings.testOutputDone'));
    } catch { showToast(t('common.error'), 'error'); }
    setTestingOutput(false);
  };

  const toggleMicMonitor = () => {
    if (micMonitoring) {
      if (micMonitorRef.current) clearInterval(micMonitorRef.current);
      micMonitorRef.current = null;
      setMicMonitoring(false);
      setLiveMicLevel(0);
    } else {
      setMicMonitoring(true);
      const poll = setInterval(async () => {
        try {
          const res = await fetch('/api/ui/setup/audio/mic-level').then(r => r.json());
          setLiveMicLevel(res.level || 0);
        } catch { /* ignore */ }
      }, 350);
      micMonitorRef.current = poll;
    }
  };

  const testInput = async () => {
    setTestingInput(true);
    setMicLevel(null);
    setPlayingBack(false);
    setCountdown(3);
    const timer = setInterval(() => {
      setCountdown(prev => {
        if (prev <= 1) { clearInterval(timer); return 0; }
        return prev - 1;
      });
    }, 1000);
    try {
      const res = await fetch('/api/ui/setup/audio/test/input', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ device: selectedInput, output_device: selectedOutput }),
      });
      clearInterval(timer);
      setCountdown(0);
      setPlayingBack(true);
      const data = await res.json();
      if (data.peak_level !== undefined) setMicLevel(data.peak_level);
      setPlayingBack(false);
      showToast(t('settings.testInputDone'));
    } catch {
      clearInterval(timer);
      setCountdown(0);
      showToast(t('common.error'), 'error');
    }
    setTestingInput(false);
  };

  return (
    <div className="space-y-8">
      <div>
        <h3 className="text-xl font-semibold mb-1">{t('settings.audioSubsystem')}</h3>
        <p className="text-sm text-zinc-400">{t('settings.audioSubsystemDesc')}</p>
      </div>

      {/* Microphone */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
        <h4 className="font-medium mb-4">{t('settings.microphone')}</h4>
        <select value={selectedInput} onChange={(e) => { setSelectedInput(e.target.value); }}
          className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-emerald-500">
          {inputs.length > 0 ? inputs.map(d => (
            <option key={d.id} value={d.id}>{d.name} ({d.type})</option>
          )) : <option>{t('settings.noDevicesFound')}</option>}
        </select>

        {/* Mic gain slider */}
        <div className="mt-4">
          <div className="flex justify-between mb-1">
            <span className="text-xs text-zinc-400">{t('settings.micGain')}</span>
            <span className="text-xs text-zinc-300 font-mono">{inputGain}%</span>
          </div>
          <input type="range" min={0} max={150} step={5} value={inputGain}
            onChange={(e) => applyGain(parseInt(e.target.value))}
            className="w-full h-1.5 rounded-full appearance-none cursor-pointer accent-emerald-500"
            style={{ background: `linear-gradient(to right, #10b981 ${inputGain / 1.5}%, #27272a ${inputGain / 1.5}%)` }} />
        </div>

        {/* Live mic level + test */}
        <div className="mt-4 flex items-center gap-3">
          <button onClick={toggleMicMonitor}
            className={`px-3 py-1.5 rounded-lg border text-sm font-medium transition-colors ${
              micMonitoring
                ? 'bg-red-500/10 border-red-500/30 text-red-400 hover:bg-red-500/20'
                : 'bg-zinc-800 border-zinc-700 hover:bg-zinc-700'
            }`}>
            {micMonitoring ? t('settings.stopMonitor') : t('settings.micMonitor')}
          </button>
          <button onClick={testInput} disabled={testingInput || playingBack || !inputs.length}
            className="px-3 py-1.5 rounded-lg bg-zinc-800 border border-zinc-700 text-sm font-medium hover:bg-zinc-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed">
            {testingInput
              ? `${t('settings.testingMic')} ${countdown > 0 ? countdown + t('settings.sec') : ''}`
              : playingBack
                ? t('settings.playingBack')
                : t('settings.testMic')}
          </button>
          {testingInput && countdown > 0 && (
            <div className="flex items-center gap-2">
              <span className="relative flex h-3 w-3">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75" />
                <span className="relative inline-flex rounded-full h-3 w-3 bg-red-500" />
              </span>
              <span className="text-xs text-red-400">{t('settings.recording')}</span>
            </div>
          )}
        </div>

        {/* Live level bar */}
        {micMonitoring && (
          <div className="mt-3 flex items-center gap-2">
            <div className="flex-1 h-3 bg-zinc-800 rounded-full overflow-hidden">
              <div className="h-full rounded-full transition-all duration-150"
                style={{
                  width: `${Math.min(liveMicLevel * 100, 100)}%`,
                  backgroundColor: liveMicLevel > 0.5 ? '#ef4444' : liveMicLevel > 0.1 ? '#f59e0b' : liveMicLevel > 0.01 ? '#10b981' : '#3f3f46',
                }} />
            </div>
            <span className="text-xs text-zinc-400 font-mono w-12 text-right">{(liveMicLevel * 100).toFixed(1)}%</span>
          </div>
        )}

        {/* Test result level */}
        {micLevel !== null && !testingInput && !micMonitoring && (
          <div className="mt-3 flex items-center gap-2">
            <div className="flex-1 h-2 bg-zinc-800 rounded-full overflow-hidden">
              <div className="h-full rounded-full transition-all duration-300"
                style={{ width: `${Math.min(micLevel * 100, 100)}%`, backgroundColor: micLevel > 0.01 ? '#10b981' : '#ef4444' }} />
            </div>
            <span className="text-xs text-zinc-400">{(micLevel * 100).toFixed(1)}%</span>
          </div>
        )}
      </div>

      {/* Speaker */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
        <h4 className="font-medium mb-4">{t('settings.speaker')}</h4>
        <select value={selectedOutput} onChange={(e) => { setSelectedOutput(e.target.value); }}
          className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-emerald-500">
          {outputs.length > 0 ? outputs.map(d => (
            <option key={d.id} value={d.id}>{d.name} ({d.type})</option>
          )) : <option>{t('settings.noDevicesFound')}</option>}
        </select>

        {/* Volume slider */}
        <div className="mt-4">
          <div className="flex justify-between mb-1">
            <span className="text-xs text-zinc-400">{t('settings.volume')}</span>
            <span className="text-xs text-zinc-300 font-mono">{outputVolume}%</span>
          </div>
          <input type="range" min={0} max={150} step={5} value={outputVolume}
            onChange={(e) => applyVolume(parseInt(e.target.value))}
            className="w-full h-1.5 rounded-full appearance-none cursor-pointer accent-emerald-500"
            style={{ background: `linear-gradient(to right, #10b981 ${outputVolume / 1.5}%, #27272a ${outputVolume / 1.5}%)` }} />
        </div>

        <div className="mt-4">
          <button onClick={testOutput} disabled={testingOutput || !outputs.length}
            className="px-3 py-1.5 rounded-lg bg-zinc-800 border border-zinc-700 text-sm font-medium hover:bg-zinc-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed">
            {testingOutput ? t('settings.testingSpeaker') : t('settings.testSpeaker')}
          </button>
        </div>
      </div>

      <button onClick={saveAudio} disabled={savingAudio} className="px-4 py-2 rounded-lg bg-emerald-500 text-zinc-950 text-sm font-medium hover:bg-emerald-400 transition-colors disabled:opacity-50 disabled:cursor-not-allowed">
        {savingAudio ? t('common.saving') : t('common.save')}
      </button>

      {/* Audio Sources */}
      <AudioSources />
    </div>
  );
}

function AudioSources() {
  const { t } = useTranslation();
  const [sources, setSources] = useState<any[]>([]);

  useEffect(() => {
    fetch('/api/ui/setup/audio/sources').then(r => r.json()).then(data => {
      setSources(data.sources || []);
    }).catch(() => {});
  }, []);

  const setVolume = async (module: string, vol: number) => {
    setSources(prev => prev.map(s => s.module === module ? { ...s, volume: vol } : s));
    await fetch('/api/ui/setup/audio/sources/volume', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ module, volume: vol }),
    }).catch(() => {});
  };

  if (sources.length === 0) return null;

  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
      <h4 className="font-medium mb-4">{t('settings.audioSources')}</h4>
      <div className="space-y-4">
        {sources.map(s => (
          <div key={s.module}>
            <div className="flex justify-between mb-1">
              <span className="text-sm text-zinc-300">{s.name}</span>
              <span className="text-xs text-zinc-400 font-mono">{s.volume}%</span>
            </div>
            <input type="range" min={0} max={100} step={1} value={s.volume}
              onChange={(e) => setVolume(s.module, parseInt(e.target.value))}
              className="w-full h-1.5 rounded-full appearance-none cursor-pointer accent-emerald-500"
              style={{ background: `linear-gradient(to right, #10b981 ${s.volume}%, #27272a ${s.volume}%)` }} />
          </div>
        ))}
      </div>
    </div>
  );
}

// ================================================================ //
//  Network Settings                                                   //
// ================================================================ //

function NetworkSettings() {
  const { t } = useTranslation();
  const showToast = useStore(s => s.showToast);
  const [netStatus, setNetStatus] = useState<any>(null);
  const [wifiNetworks, setWifiNetworks] = useState<any[]>([]);
  const [scanning, setScanning] = useState(false);
  const [toggling, setToggling] = useState(false);
  const [connecting, setConnecting] = useState<string | null>(null);
  const [password, setPassword] = useState('');
  const [selectedSsid, setSelectedSsid] = useState('');
  const [connectError, setConnectError] = useState('');

  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch('/api/ui/setup/network/status');
      setNetStatus(await res.json());
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { fetchStatus(); }, [fetchStatus]);

  const wifiEnabled = netStatus?.wifi?.enabled ?? false;
  const wifiAdapterFound = netStatus?.wifi?.adapter_found ?? false;

  const toggleWifi = async () => {
    setToggling(true);
    try {
      const res = await fetch('/api/ui/setup/wifi/toggle', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enable: !wifiEnabled }),
      });
      if (res.ok) {
        // Wait for adapter to settle, then refresh status + scan
        await new Promise(r => setTimeout(r, 2000));
        await fetchStatus();
        if (!wifiEnabled) scanWifi();
        showToast(t('settings.saved'));
      }
    } catch { showToast(t('common.error'), 'error'); }
    setToggling(false);
  };

  const scanWifi = async () => {
    setScanning(true);
    try {
      const res = await fetch('/api/ui/setup/wifi/scan');
      const data = await res.json();
      setWifiNetworks(data.networks || []);
    } catch { /* ignore */ }
    setScanning(false);
  };

  const connectWifi = async () => {
    if (!selectedSsid) return;
    setConnecting(selectedSsid);
    setConnectError('');
    try {
      const res = await fetch('/api/ui/setup/wifi/connect', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ssid: selectedSsid, password }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setConnectError(body.detail || t('common.error'));
      } else {
        setPassword('');
        setSelectedSsid('');
        showToast(t('settings.wifiConnected'));
        await fetchStatus();
      }
    } catch (e: any) { setConnectError(e.message); showToast(e.message, 'error'); }
    setConnecting(null);
  };

  return (
    <div className="space-y-8">
      <div>
        <h3 className="text-xl font-semibold mb-1">{t('settings.networkTitle')}</h3>
        <p className="text-sm text-zinc-400">{t('settings.networkDesc')}</p>
      </div>

      {/* Current Status */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
        <h4 className="font-medium mb-4">{t('settings.networkStatus')}</h4>
        {netStatus ? (
          <div className="space-y-3">
            <div className="flex items-center justify-between text-sm">
              <span className="text-zinc-400">{t('settings.internet')}</span>
              <span className={netStatus.internet ? "text-emerald-500" : "text-red-400"}>
                {netStatus.internet ? t('settings.connected') : t('settings.disconnected')}
              </span>
            </div>
            {netStatus.ethernet?.connected && (
              <div className="flex items-center justify-between text-sm">
                <span className="text-zinc-400">Ethernet ({netStatus.ethernet.interface})</span>
                <span className="text-zinc-200 font-mono text-xs">{netStatus.ethernet.ip}</span>
              </div>
            )}
            {netStatus.wifi?.connected && (
              <div className="flex items-center justify-between text-sm">
                <span className="text-zinc-400">Wi-Fi ({netStatus.wifi.ssid})</span>
                <span className="text-zinc-200 font-mono text-xs">{netStatus.wifi.ip}</span>
              </div>
            )}
          </div>
        ) : (
          <div className="text-sm text-zinc-500">{t('common.loading')}</div>
        )}
      </div>

      {/* WiFi Toggle + Networks */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-3">
            <Wifi size={18} className={wifiEnabled ? "text-emerald-500" : "text-zinc-500"} />
            <h4 className="font-medium">Wi-Fi</h4>
          </div>
          {wifiAdapterFound ? (
            <button onClick={toggleWifi} disabled={toggling}
              className={cn(
                "relative w-11 h-6 rounded-full transition-colors",
                wifiEnabled ? "bg-emerald-500" : "bg-zinc-700",
                toggling && "opacity-50"
              )}>
              <span className={cn(
                "absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full transition-transform shadow-sm",
                wifiEnabled && "translate-x-5"
              )} />
            </button>
          ) : (
            <span className="text-xs text-zinc-500">{t('wizard.wifiAdapterNotFound')}</span>
          )}
        </div>

        {wifiAdapterFound && !wifiEnabled && (
          <p className="text-sm text-zinc-500">{t('wizard.wifiAdapterOff')}</p>
        )}

        {wifiEnabled && (
          <>
            <div className="flex items-center justify-between mb-3">
              <span className="text-sm text-zinc-400">
                {netStatus?.wifi?.connected
                  ? `${t('settings.connected')}: ${netStatus.wifi.ssid}`
                  : t('settings.disconnected')}
              </span>
              <button onClick={scanWifi} disabled={scanning}
                className="text-xs text-zinc-400 hover:text-zinc-200 flex items-center gap-1.5 transition-colors">
                <RefreshCw size={14} className={scanning ? 'animate-spin' : ''} />
                {t('settings.scan')}
              </button>
            </div>

            <div className="space-y-2 max-h-[240px] overflow-y-auto">
              {wifiNetworks.map(net => (
                <button key={net.ssid} onClick={() => { setSelectedSsid(net.ssid); setPassword(''); setConnectError(''); }}
                  className={cn("w-full p-3 rounded-lg border flex items-center justify-between text-sm transition-all",
                    selectedSsid === net.ssid ? "border-emerald-500 bg-emerald-500/10" : "border-zinc-800 bg-zinc-950 hover:border-zinc-700")}>
                  <div className="flex items-center gap-2">
                    <Wifi size={16} className={net.connected ? "text-emerald-500" : "text-zinc-400"} />
                    <span>{net.ssid}</span>
                    {net.connected && <span className="text-xs text-emerald-500">{t('settings.connected')}</span>}
                    {net.security && <Lock size={12} className="text-zinc-500" />}
                  </div>
                  <span className="text-xs text-zinc-500">{net.signal}%</span>
                </button>
              ))}
              {wifiNetworks.length === 0 && !scanning && (
                <div className="text-center text-sm text-zinc-500 py-4">{t('settings.clickScan')}</div>
              )}
              {scanning && (
                <div className="text-center text-sm text-zinc-500 py-4">{t('wizard.wifiScanning')}</div>
              )}
            </div>

            {selectedSsid && (
              <div className="mt-4 space-y-3">
                <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                  placeholder={t('settings.wifiPassword')}
                  onKeyDown={(e) => e.key === 'Enter' && connectWifi()}
                  className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-emerald-500" />
                {connectError && <div className="text-xs text-red-400">{connectError}</div>}
                <button onClick={connectWifi} disabled={connecting !== null}
                  className="px-4 py-2 rounded-lg bg-emerald-500 text-zinc-950 text-sm font-medium hover:bg-emerald-400 disabled:opacity-50">
                  {connecting ? t('settings.connecting') : t('settings.connect')}
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

// ================================================================ //
//  Users & Presence Settings                                        //
// ================================================================ //

const PRESENCE_API = '/api/ui/modules/presence-detection';

interface PresenceUser {
  user_id: string;
  name: string;
  state: string;
  devices: { type: string; address: string; name?: string }[];
  last_seen: string | null;
  confidence: number;
  detected: boolean;
  away_in_sec: number | null;
}

interface PushSub {
  user_id: string;
  endpoint: string;
  platform: string;
  created_at: string;
}

interface NetworkDevice {
  ip: string;
  mac: string;
  hostname: string;
  manufacturer: string;
}

function UsersSettings() {
  return <UsersPanel />;
}
// ================================================================ //
//  System Settings                                                    //
// ================================================================ //

function SystemSettings() {
  const { t } = useTranslation();
  const showToast = useStore(s => s.showToast);
  const [autoStopRam, setAutoStopRam] = useState(true);
  const [stopLlmTemp, setStopLlmTemp] = useState(true);
  const [resetting, setResetting] = useState(false);
  const [showReprovision, setShowReprovision] = useState(false);

  const saveSetting = async (key: string, value: boolean) => {
    try {
      await fetch('/api/ui/setup/config/update', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ section: 'system', key, value }),
      });
      showToast(t('settings.saved'));
    } catch { showToast(t('common.error'), 'error'); }
  };

  const resetWizard = async () => {
    if (!confirm(t('settings.resetWizardConfirm'))) return;
    setResetting(true);
    try {
      const res = await fetch('/api/ui/wizard/reset', { method: 'POST' });
      if (res.ok) {
        window.location.href = '/';
      }
    } catch { /* ignore */ } finally {
      setResetting(false);
    }
  };

  return (
    <div className="space-y-8">
      <div>
        <h3 className="text-xl font-semibold mb-1">{t('settings.systemTitle')}</h3>
        <p className="text-sm text-zinc-400">{t('settings.systemDesc')}</p>
      </div>
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
        <h4 className="font-medium mb-4">{t('settings.degradationStrategy')}</h4>
        <div className="space-y-4">
          <label className="flex items-center gap-3">
            <input type="checkbox" checked={autoStopRam}
              onChange={(e) => { setAutoStopRam(e.target.checked); saveSetting('auto_stop_low_ram', e.target.checked); }}
              className="rounded border-zinc-700 bg-zinc-950 text-emerald-500 focus:ring-emerald-500 focus:ring-offset-zinc-900" />
            <span className="text-sm text-zinc-300">{t('settings.autoStopAutomation')}</span>
          </label>
          <label className="flex items-center gap-3">
            <input type="checkbox" checked={stopLlmTemp}
              onChange={(e) => { setStopLlmTemp(e.target.checked); saveSetting('stop_llm_high_temp', e.target.checked); }}
              className="rounded border-zinc-700 bg-zinc-950 text-emerald-500 focus:ring-emerald-500 focus:ring-offset-zinc-900" />
            <span className="text-sm text-zinc-300">{t('settings.stopLlmOnHighTemp')}</span>
          </label>
        </div>
      </div>

      {/* Re-provision: re-download models / reinstall services */}
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
        <h4 className="font-medium mb-2">{t('settings.rerunSetupTitle')}</h4>
        <p className="text-sm text-zinc-400 mb-4">{t('settings.rerunSetupDesc')}</p>
        <button
          onClick={() => setShowReprovision(true)}
          className="px-4 py-2 rounded-lg text-sm font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 hover:bg-emerald-500/20 transition-colors flex items-center gap-2"
        >
          <RefreshCw size={14} />
          {t('settings.rerunSetupBtn')}
        </button>
      </div>

      {/* Re-provision modal */}
      {showReprovision && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm">
          <div className="bg-zinc-950 border border-zinc-800 rounded-2xl p-6 w-full max-w-md mx-4 max-h-[90vh] overflow-y-auto">
            <ProvisionProgress
              onDone={() => {
                setShowReprovision(false);
                showToast(t('settings.rerunSetupDone'));
              }}
              onSkip={() => setShowReprovision(false)}
              showActions={true}
            />
          </div>
        </div>
      )}

      <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
        <h4 className="font-medium mb-2">{t('settings.resetWizardTitle')}</h4>
        <p className="text-sm text-zinc-400 mb-4">{t('settings.resetWizardDesc')}</p>
        <button
          onClick={resetWizard}
          disabled={resetting}
          className="px-4 py-2 rounded-lg text-sm font-medium bg-amber-500/10 text-amber-400 border border-amber-500/20 hover:bg-amber-500/20 transition-colors disabled:opacity-50"
        >
          {resetting ? t('common.loading') : t('settings.resetWizardBtn')}
        </button>
      </div>
    </div>
  );
}

// ================================================================ //
//  System Modules Settings                                           //
// ================================================================ //
function humanizeModuleName(name: string): string {
  return name
    .split(/[-_]/)
    .filter(Boolean)
    .map(w => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

type SystemMod = {
  name: string;
  status: string;
  port: number;
  ui?: { icon?: string; widget?: { file?: string }; settings?: string };
};

function SystemModRow({
  m, isSelected, isPinned, hasWidget, onSelect, onTogglePin,
}: {
  m: SystemMod;
  isSelected: boolean;
  isPinned: boolean;
  hasWidget: boolean;
  onSelect: () => void;
  onTogglePin: () => void;
}) {
  const { t } = useTranslation();
  const [iconFailed, setIconFailed] = useState(false);
  const isRunning = m.status === 'RUNNING';
  const friendly = t(`systemModuleNames.${m.name}`, { defaultValue: humanizeModuleName(m.name) });
  const showIcon = !!m.ui?.icon && !iconFailed;

  return (
    <div
      onClick={onSelect}
      style={{
        padding: '8px 10px', borderRadius: 8,
        background: isSelected ? 'var(--sf2)' : 'var(--sf)',
        border: `1px solid ${isSelected ? 'var(--b2)' : 'var(--b)'}`,
        cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 10,
        transition: 'all .15s',
      }}
    >
      <div style={{
        position: 'relative', width: 28, height: 28, flexShrink: 0,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        {showIcon ? (
          <img
            src={`/api/ui/modules/${m.name}/icon`}
            alt={m.name}
            onError={() => setIconFailed(true)}
            style={{ width: 24, height: 24, objectFit: 'contain', borderRadius: 5 }}
          />
        ) : (
          <span style={{ fontSize: 18, lineHeight: 1 }}>⚙️</span>
        )}
        <div style={{
          position: 'absolute', right: -1, bottom: -1,
          width: 7, height: 7, borderRadius: '50%',
          background: isRunning ? 'var(--gr)' : 'var(--tx3)',
          border: '1.5px solid var(--sf)',
        }} />
      </div>
      <span style={{
        fontSize: 12, fontWeight: 500, color: 'var(--tx)',
        flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
      }}>
        {friendly}
      </span>
      {hasWidget && (
        <button
          onClick={e => {
            e.stopPropagation();
            onTogglePin();
          }}
          style={{
            fontSize: 9, padding: '2px 7px', borderRadius: 4,
            background: isPinned ? 'rgba(79,140,247,.15)' : 'var(--sf3)',
            color: isPinned ? 'var(--ac)' : 'var(--tx3)',
            border: `1px solid ${isPinned ? 'rgba(79,140,247,.3)' : 'var(--b)'}`,
            cursor: 'pointer', fontWeight: 500, flexShrink: 0,
          }}
        >
          {isPinned ? t('settings.pinned') : t('settings.pin')}
        </button>
      )}
    </div>
  );
}

function SystemModulesSettings() {
  const { t } = useTranslation();
  const modules = useStore(s => s.modules);
  const fetchModules = useStore(s => s.fetchModules);
  const widgetLayout = useStore(s => s.widgetLayout);
  const pinModule = useStore(s => s.pinModule);
  const unpinModule = useStore(s => s.unpinModule);
  const [selectedMod, setSelectedMod] = useState<string | null>(null);

  useEffect(() => { fetchModules(); }, [fetchModules]);

  const systemMods = modules.filter(m => m.type === 'SYSTEM' && m.name !== 'presence-detection' && m.name !== 'user-manager');
  const selected = systemMods.find(m => m.name === selectedMod);

  return (
    <div style={{
      height: '100%', minHeight: 0,
      display: 'flex', flexDirection: 'column',
      padding: 12, gap: 12,
    }}>
      <div style={{ flexShrink: 0 }}>
        <h3 className="text-xl font-semibold mb-1">{t('settings.systemModules')}</h3>
        <p className="text-sm text-zinc-400">{t('settings.systemModulesDesc')}</p>
      </div>

      <div style={{ display: 'flex', gap: 16, flex: 1, minHeight: 0 }}>
        {/* Module list */}
        <div style={{ width: 220, flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 4, overflowY: 'auto' }}>
          {systemMods.length === 0 ? (
            <div style={{ fontSize: 12, color: 'var(--tx3)', padding: '8px 0' }}>
              {t('settings.noSystemModules')}
            </div>
          ) : systemMods.map(m => {
            const isPinned = widgetLayout.pinned.includes(m.name);
            const isSelected = m.name === selectedMod;
            const hasWidget = !!m.ui?.widget?.file;
            return (
              <SystemModRow
                key={m.name}
                m={m}
                isSelected={isSelected}
                isPinned={isPinned}
                hasWidget={hasWidget}
                onSelect={() => setSelectedMod(isSelected ? null : m.name)}
                onTogglePin={() => isPinned ? unpinModule(m.name) : pinModule(m.name)}
              />
            );
          })}
        </div>

        {/* Settings iframe */}
        <div style={{
          flex: 1, background: 'var(--sf)', border: '1px solid var(--b)',
          borderRadius: 12, overflow: 'hidden',
        }}>
          {selected && selected.ui?.settings ? (
            <iframe
              key={selected.name}
              src={`/api/ui/modules/${selected.name}/settings?_=${Date.now()}`}
              style={{ width: '100%', height: '100%', border: 'none', display: 'block' }}
              title={`${selected.name} settings`}
              allow="geolocation"
            />
          ) : selected ? (
            <div style={{
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              height: '100%', color: 'var(--tx3)', fontSize: 12,
            }}>
              {t('settings.noSettingsAvailable')}
            </div>
          ) : (
            <div style={{
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              height: '100%', color: 'var(--tx3)', fontSize: 12,
            }}>
              {t('settings.selectModuleToConfig')}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

