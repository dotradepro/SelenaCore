import { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { useTranslation } from 'react-i18next';
import { useStore } from '../store/useStore';
import { Check, ChevronRight, Wifi, Globe, Mic, User, Cloud, Download, Activity, AlertCircle } from 'lucide-react';
import { cn } from '../lib/utils';

const STEP_ICONS = [Globe, Wifi, HomeIcon, Globe, Mic, Mic, User, Cloud, Download];

// Map frontend step number to backend step name + data builder
const STEP_MAP: Record<number, { name: string; buildData: (f: FormData) => Record<string, string> }> = {
  1: { name: 'language', buildData: (f) => ({ language: f.lang }) },
  2: { name: 'wifi', buildData: (f) => ({ ssid: f.wifi, password: f.wifiPassword }) },
  3: { name: 'device_name', buildData: (f) => ({ name: f.name }) },
  4: { name: 'timezone', buildData: (f) => ({ timezone: f.timezone }) },
  5: { name: 'stt_model', buildData: (f) => ({ model: f.stt }) },
  6: { name: 'tts_voice', buildData: (f) => ({ voice: f.tts }) },
  7: { name: 'admin_user', buildData: (f) => ({ username: f.username, pin: f.pin }) },
  8: { name: 'platform', buildData: (f) => ({ device_hash: f.platformHash }) },
  9: { name: 'import', buildData: (f) => ({ source: f.importSource }) },
};

interface FormData {
  lang: string;
  wifi: string;
  wifiPassword: string;
  name: string;
  timezone: string;
  stt: string;
  tts: string;
  username: string;
  pin: string;
  platformHash: string;
  importSource: string;
}

function HomeIcon(props: any) {
  return <svg {...props} xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /><polyline points="9 22 9 12 15 12 15 22" /></svg>;
}

export default function Wizard() {
  const { t } = useTranslation();
  const selectedLanguage = useStore((state) => state.selectedLanguage);
  const setSelectedLanguage = useStore((state) => state.setSelectedLanguage);
  const [step, setStep] = useState(1);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const setConfigured = useStore((state) => state.setConfigured);
  const setUser = useStore((state) => state.setUser);

  const [formData, setFormData] = useState<FormData>({
    lang: selectedLanguage,
    wifi: '',
    wifiPassword: '',
    name: t('wizard.defaultHomeName'),
    timezone: 'Europe/Kyiv',
    stt: 'base',
    tts: 'ru_irina',
    username: 'admin',
    pin: '',
    platformHash: '',
    importSource: '',
  });



  const nextStep = async () => {
    const mapping = STEP_MAP[step];
    if (!mapping) return;

    setError(null);
    setSubmitting(true);
    try {
      const resp = await fetch('/api/ui/wizard/step', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ step: mapping.name, data: mapping.buildData(formData) }),
      });
      if (!resp.ok && resp.status !== 409) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body?.detail ?? `${t('common.error')} ${resp.status}`);
      }
      if (step === 9) {
        setUser({ name: formData.username, role: 'admin' });
        setConfigured(true);
      } else {
        setStep(s => s + 1);
      }
    } catch (e: any) {
      setError(e.message ?? t('wizard.unknownError'));
    } finally {
      setSubmitting(false);
    }
  };

  const skipStep = async () => {
    // Steps 8 and 9 can be skipped — send empty data
    const mapping = STEP_MAP[step];
    if (!mapping) return;
    setError(null);
    setSubmitting(true);
    try {
      await fetch('/api/ui/wizard/step', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ step: mapping.name, data: {} }),
      });
      if (step === 9) {
        setUser({ name: formData.username || 'Admin', role: 'admin' });
        setConfigured(true);
      } else {
        setStep(s => s + 1);
      }
    } catch {
      // skip anyway
      if (step === 9) {
        setUser({ name: formData.username || 'Admin', role: 'admin' });
        setConfigured(true);
      } else {
        setStep(s => s + 1);
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-50 flex flex-col items-center justify-center p-4 font-sans">
      <div className="w-full max-w-3xl">
        {/* Header */}
        <div className="text-center mb-12">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-emerald-500/10 text-emerald-500 mb-6">
            <Activity size={32} />
          </div>
          <h1 className="text-3xl font-semibold tracking-tight mb-2">{t('wizard.coreTitle')}</h1>
          <p className="text-zinc-400">{t('wizard.initialSetup')}</p>
        </div>

        {/* Progress Bar */}
        <div className="flex items-center justify-between mb-12 relative">
          <div className="absolute left-0 top-1/2 -translate-y-1/2 w-full h-1 bg-zinc-800 -z-10 rounded-full overflow-hidden">
            <motion.div
              className="h-full bg-emerald-500"
              initial={{ width: 0 }}
              animate={{ width: `${((step - 1) / (STEP_ICONS.length - 1)) * 100}%` }}
              transition={{ duration: 0.3 }}
            />
          </div>
          {STEP_ICONS.map((Icon, idx) => {
            const sid = idx + 1;
            const isActive = sid === step;
            const isPast = sid < step;
            const stepTitleKeys = ['stepLanguage', 'stepWifi', 'stepHomeName', 'stepTimezone', 'stepStt', 'stepTts', 'stepUser', 'stepPlatform', 'stepImport'];
            return (
              <div key={sid} className="flex flex-col items-center gap-2">
                <div className={cn(
                  "w-10 h-10 rounded-full flex items-center justify-center text-sm font-medium transition-colors border-2",
                  isActive ? "bg-zinc-900 border-emerald-500 text-emerald-500" :
                    isPast ? "bg-emerald-500 border-emerald-500 text-zinc-950" :
                      "bg-zinc-900 border-zinc-800 text-zinc-500"
                )}>
                  {isPast ? <Check size={18} /> : sid}
                </div>
                <span className={cn(
                  "text-xs font-medium absolute mt-12 w-20 text-center",
                  isActive ? "text-zinc-50" : "text-zinc-500"
                )}>
                  {isActive && t(`wizard.${stepTitleKeys[idx]}`)}
                </span>
              </div>
            );
          })}
        </div>

        {/* Content Area */}
        <div className="bg-zinc-900/50 border border-zinc-800 rounded-2xl p-8 backdrop-blur-sm min-h-[400px] flex flex-col">
          <AnimatePresence mode="wait">
            <motion.div
              key={step}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              transition={{ duration: 0.2 }}
              className="flex-1"
            >
              {step === 1 && (
                <div className="space-y-6">
                  <h2 className="text-xl font-medium">{t('wizard.selectLanguage')}</h2>
                  <p className="text-zinc-400 text-sm">{t('wizard.languageDesc')}</p>
                  <div className="space-y-3">
                    {[
                      { id: 'uk', name: 'Українська' },
                      { id: 'en', name: 'English' },
                    ].map(lang => (
                      <button
                        key={lang.id}
                        onClick={() => { setFormData({ ...formData, lang: lang.id }); setSelectedLanguage(lang.id); }}
                        className={cn(
                          "w-full p-4 rounded-xl border flex items-center justify-between transition-all",
                          formData.lang === lang.id
                            ? "border-emerald-500 bg-emerald-500/10"
                            : "border-zinc-800 bg-zinc-900 hover:border-zinc-700"
                        )}
                      >
                        <span className="font-medium">{lang.name}</span>
                        {formData.lang === lang.id && <Check size={20} className="text-emerald-500" />}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {step === 2 && (
                <div className="space-y-6">
                  <h2 className="text-xl font-medium">{t('wizard.wifiTitle')}</h2>
                  <p className="text-zinc-400 text-sm">{t('wizard.wifiDesc')}</p>
                  <div className="space-y-3">
                    {['Home_Network_5G', 'Keenetic-1234', 'Guest_Net'].map(net => (
                      <button
                        key={net}
                        onClick={() => setFormData({ ...formData, wifi: net })}
                        className={cn(
                          "w-full p-4 rounded-xl border flex items-center justify-between transition-all",
                          formData.wifi === net
                            ? "border-emerald-500 bg-emerald-500/10"
                            : "border-zinc-800 bg-zinc-900 hover:border-zinc-700"
                        )}
                      >
                        <div className="flex items-center gap-3">
                          <Wifi size={20} className={formData.wifi === net ? "text-emerald-500" : "text-zinc-400"} />
                          <span className="font-medium">{net}</span>
                        </div>
                        {formData.wifi === net && <Check size={20} className="text-emerald-500" />}
                      </button>
                    ))}
                  </div>
                  {formData.wifi && (
                    <div>
                      <label className="block text-sm font-medium text-zinc-400 mb-1.5">{t('wizard.wifiPassword')}</label>
                      <input
                        type="password"
                        value={formData.wifiPassword}
                        onChange={(e) => setFormData({ ...formData, wifiPassword: e.target.value })}
                        className="w-full bg-zinc-950 border border-zinc-800 rounded-xl px-4 py-3 text-zinc-50 focus:outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 transition-all"
                        placeholder={t('wizard.wifiPasswordPlaceholder')}
                      />
                    </div>
                  )}
                </div>
              )}

              {step === 3 && (
                <div className="space-y-6">
                  <h2 className="text-xl font-medium">{t('wizard.deviceNameTitle')}</h2>
                  <p className="text-zinc-400 text-sm">{t('wizard.deviceNameDesc')}</p>
                  <input
                    type="text"
                    value={formData.name}
                    onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                    className="w-full bg-zinc-950 border border-zinc-800 rounded-xl px-4 py-3 text-zinc-50 focus:outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 transition-all"
                    placeholder={t('wizard.deviceNamePlaceholder')}
                  />
                </div>
              )}

              {step === 4 && (
                <div className="space-y-6">
                  <h2 className="text-xl font-medium">{t('wizard.timezoneTitle')}</h2>
                  <p className="text-zinc-400 text-sm">{t('wizard.timezoneDesc')}</p>
                  <select
                    value={formData.timezone}
                    onChange={(e) => setFormData({ ...formData, timezone: e.target.value })}
                    className="w-full bg-zinc-950 border border-zinc-800 rounded-xl px-4 py-3 text-zinc-50 focus:outline-none focus:border-emerald-500 transition-all appearance-none"
                  >
                    <option value="Europe/Moscow">Europe/Moscow (MSK)</option>
                    <option value="Europe/Kyiv">Europe/Kyiv (EET)</option>
                    <option value="Europe/London">Europe/London (GMT)</option>
                  </select>
                </div>
              )}

              {step === 5 && (
                <div className="space-y-6">
                  <h2 className="text-xl font-medium">{t('wizard.sttTitle')}</h2>
                  <p className="text-zinc-400 text-sm">{t('wizard.sttDesc')}</p>
                  <div className="space-y-3">
                    {[
                      { id: 'tiny', name: t('wizard.sttTiny'), desc: t('wizard.sttTinyDesc'), ram: '~150 MB' },
                      { id: 'base', name: t('wizard.sttBase'), desc: t('wizard.sttBaseDesc'), ram: '~250 MB' },
                      { id: 'small', name: t('wizard.sttSmall'), desc: t('wizard.sttSmallDesc'), ram: '~500 MB' },
                    ].map(m => (
                      <button
                        key={m.id}
                        onClick={() => setFormData({ ...formData, stt: m.id })}
                        className={cn(
                          "w-full p-4 rounded-xl border flex items-center justify-between text-left transition-all",
                          formData.stt === m.id
                            ? "border-emerald-500 bg-emerald-500/10"
                            : "border-zinc-800 bg-zinc-900 hover:border-zinc-700"
                        )}
                      >
                        <div>
                          <div className="font-medium flex items-center gap-2">
                            {m.name}
                            <span className="text-xs px-2 py-0.5 rounded-full bg-zinc-800 text-zinc-400">{m.ram}</span>
                          </div>
                          <div className="text-sm text-zinc-400 mt-1">{m.desc}</div>
                        </div>
                        {formData.stt === m.id && <Check size={20} className="text-emerald-500" />}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {step === 6 && (
                <div className="space-y-6">
                  <h2 className="text-xl font-medium">{t('wizard.ttsTitle')}</h2>
                  <p className="text-zinc-400 text-sm">{t('wizard.ttsDesc')}</p>
                  <div className="grid grid-cols-2 gap-4">
                    {[
                      { id: 'ru_irina', name: t('wizard.ttsIrina') },
                      { id: 'ru_dmitry', name: t('wizard.ttsDmitry') },
                      { id: 'ru_ruslan', name: t('wizard.ttsRuslan') },
                      { id: 'ru_kseniya', name: t('wizard.ttsKseniya') },
                    ].map(v => (
                      <button
                        key={v.id}
                        onClick={() => setFormData({ ...formData, tts: v.id })}
                        className={cn(
                          "p-4 rounded-xl border flex items-center justify-between transition-all",
                          formData.tts === v.id
                            ? "border-emerald-500 bg-emerald-500/10 text-emerald-500"
                            : "border-zinc-800 bg-zinc-900 hover:border-zinc-700"
                        )}
                      >
                        <span className="font-medium">{v.name}</span>
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {step === 7 && (
                <div className="space-y-6">
                  <h2 className="text-xl font-medium">{t('wizard.userTitle')}</h2>
                  <p className="text-zinc-400 text-sm">{t('wizard.userDesc')}</p>
                  <div className="space-y-4">
                    <div>
                      <label className="block text-sm font-medium text-zinc-400 mb-1.5">{t('wizard.userName')}</label>
                      <input
                        type="text"
                        value={formData.username}
                        onChange={(e) => setFormData({ ...formData, username: e.target.value })}
                        className="w-full bg-zinc-950 border border-zinc-800 rounded-xl px-4 py-3 text-zinc-50 focus:outline-none focus:border-emerald-500 transition-all"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-zinc-400 mb-1.5">{t('wizard.userPin')}</label>
                      <input
                        type="password"
                        maxLength={8}
                        value={formData.pin}
                        onChange={(e) => setFormData({ ...formData, pin: e.target.value.replace(/\D/g, '') })}
                        className="w-full bg-zinc-950 border border-zinc-800 rounded-xl px-4 py-3 text-zinc-50 focus:outline-none focus:border-emerald-500 transition-all tracking-widest font-mono"
                        placeholder={t('wizard.userPinPlaceholder')}
                      />
                    </div>
                  </div>
                </div>
              )}

              {step === 8 && (
                <div className="space-y-6">
                  <h2 className="text-xl font-medium">{t('wizard.platformTitle')}</h2>
                  <p className="text-zinc-400 text-sm">{t('wizard.platformDesc')}</p>
                  <div className="flex flex-col items-center justify-center p-8 border border-zinc-800 border-dashed rounded-xl bg-zinc-900/50">
                    <div className="w-48 h-48 bg-white rounded-xl p-2 mb-4 flex items-center justify-center">
                      {/* Mock QR Code */}
                      <div className="w-full h-full bg-zinc-200 grid grid-cols-5 grid-rows-5 gap-1 p-1">
                        {Array.from({ length: 25 }).map((_, i) => (
                          <div key={i} className={Math.random() > 0.5 ? "bg-black" : "bg-transparent"} />
                        ))}
                      </div>
                    </div>
                    <p className="text-sm text-zinc-400 text-center">{t('wizard.platformQrHint')}</p>
                  </div>
                </div>
              )}

              {step === 9 && (
                <div className="space-y-6">
                  <h2 className="text-xl font-medium">{t('wizard.importTitle')}</h2>
                  <p className="text-zinc-400 text-sm">{t('wizard.importDesc')}</p>
                  <div className="grid grid-cols-2 gap-4">
                    {[
                      { id: 'ha', name: t('wizard.importHa'), desc: t('wizard.importLocal') },
                      { id: 'tuya', name: t('wizard.importTuya'), desc: t('wizard.importCloud') },
                      { id: 'hue', name: t('wizard.importHue'), desc: t('wizard.importLocal') },
                      { id: 'mqtt', name: t('wizard.importMqtt'), desc: t('wizard.importLocal') },
                    ].map(sys => (
                      <button
                        key={sys.id}
                        className="p-4 rounded-xl border border-zinc-800 bg-zinc-900 hover:border-zinc-700 text-left transition-all group"
                      >
                        <div className="font-medium group-hover:text-emerald-400 transition-colors">{sys.name}</div>
                        <div className="text-xs text-zinc-500 mt-1">{sys.desc}</div>
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </motion.div>
          </AnimatePresence>

          {/* Footer Actions */}
          <div className="mt-8 pt-6 border-t border-zinc-800 space-y-3">
            {error && (
              <div className="flex items-center gap-2 text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-4 py-2.5">
                <AlertCircle size={16} className="shrink-0" />
                <span>{error}</span>
              </div>
            )}
            <div className="flex items-center justify-between">
              <button
                onClick={() => {
                  if (step === 1) {
                    useStore.getState().setSetupStage('landing');
                  } else {
                    setStep(s => Math.max(1, s - 1));
                  }
                  setError(null);
                }}
                disabled={submitting}
                className="px-6 py-2.5 rounded-lg text-sm font-medium transition-colors text-zinc-400 hover:text-zinc-50 hover:bg-zinc-800 disabled:opacity-50"
              >
                {t('common.back')}
              </button>
              <div className="flex items-center gap-3">
                {(step === 8 || step === 9) && (
                  <button
                    onClick={skipStep}
                    disabled={submitting}
                    className="px-6 py-2.5 rounded-lg text-sm font-medium text-zinc-400 hover:text-zinc-50 hover:bg-zinc-800 transition-colors disabled:opacity-50"
                  >
                    {t('common.skip')}
                  </button>
                )}
                <button
                  onClick={nextStep}
                  disabled={submitting || (step === 7 && (!formData.username || formData.pin.length < 4))}
                  className="px-6 py-2.5 rounded-lg text-sm font-medium bg-emerald-500 text-zinc-950 hover:bg-emerald-400 transition-colors flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed min-w-[100px] justify-center"
                >
                  {submitting ? (
                    <div className="w-4 h-4 border-2 border-zinc-950 border-t-transparent rounded-full animate-spin" />
                  ) : (
                    <>
                      {step === 9 ? t('common.finish') : t('common.next')}
                      {step !== 9 && <ChevronRight size={16} />}
                    </>
                  )}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
