import { Routes, Route, Link, useLocation } from 'react-router-dom';
import { Mic, Volume2, Network, Users, Activity, Shield } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { cn } from '../lib/utils';

export default function Settings() {
  const { t } = useTranslation();
  const location = useLocation();

  const tabs = [
    { id: 'voice', label: t('settings.voiceAndLlm'), icon: Mic, path: '/settings/voice' },
    { id: 'audio', label: t('settings.audio'), icon: Volume2, path: '/settings/audio' },
    { id: 'network', label: t('settings.networkAndVpn'), icon: Network, path: '/settings/network' },
    { id: 'users', label: t('settings.users'), icon: Users, path: '/settings/users' },
    { id: 'system', label: t('settings.system'), icon: Activity, path: '/settings/system' },
    { id: 'security', label: t('settings.security'), icon: Shield, path: '/settings/security' },
  ];

  return (
    <div className="max-w-6xl mx-auto flex gap-8 h-full">
      {/* Settings Sidebar */}
      <div className="w-64 shrink-0 space-y-1">
        <h2 className="text-lg font-semibold mb-4 px-3">{t('settings.title')}</h2>
        {tabs.map((tab) => {
          const isActive = location.pathname.includes(tab.path) || (location.pathname === '/settings' && tab.id === 'voice');
          return (
            <Link
              key={tab.id}
              to={tab.path}
              className={cn(
                "flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors",
                isActive
                  ? "bg-zinc-800 text-zinc-50"
                  : "text-zinc-400 hover:text-zinc-50 hover:bg-zinc-800/50"
              )}
            >
              <tab.icon size={18} />
              {tab.label}
            </Link>
          );
        })}
      </div>

      {/* Settings Content */}
      <div className="flex-1 bg-zinc-900/30 border border-zinc-800 rounded-2xl p-8 overflow-auto">
        <Routes>
          <Route path="/" element={<VoiceSettings />} />
          <Route path="/voice" element={<VoiceSettings />} />
          <Route path="/audio" element={<AudioSettings />} />
          <Route path="/system" element={<SystemSettings />} />
          <Route path="*" element={<div className="text-zinc-400">{t('common.inDevelopment')}</div>} />
        </Routes>
      </div>
    </div>
  );
}

function VoiceSettings() {
  const { t } = useTranslation();
  return (
    <div className="space-y-8">
      <div>
        <h3 className="text-xl font-semibold mb-1">{t('settings.voiceAssistant')}</h3>
        <p className="text-sm text-zinc-400">{t('settings.voiceAssistantDesc')}</p>
      </div>

      <div className="space-y-6">
        <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
          <h4 className="font-medium mb-4">{t('settings.wakeWord')}</h4>
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm text-zinc-300">{t('settings.wakeWordLabel')}</div>
              <div className="text-xs text-zinc-500 mt-1">{t('settings.wakeWordDesc')}</div>
            </div>
            <select className="bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-emerald-500">
              <option>{t('wakeWords.home')}</option>
              <option>{t('wakeWords.alice')}</option>
              <option>{t('wakeWords.computer')}</option>
            </select>
          </div>
        </div>

        <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
          <h4 className="font-medium mb-4">{t('settings.llmRouter')}</h4>
          <div className="flex items-center justify-between mb-4">
            <div>
              <div className="text-sm text-zinc-300">{t('settings.localLlm')}</div>
              <div className="text-xs text-zinc-500 mt-1">{t('settings.localLlmDesc')}</div>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs text-emerald-500 bg-emerald-500/10 px-2 py-1 rounded-md font-medium">{t('settings.llmActive')}</span>
            </div>
          </div>
          <select className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-emerald-500">
            <option>phi-3-mini (3.8B int4)</option>
            <option>gemma-2b</option>
          </select>
        </div>
      </div>
    </div>
  );
}

function AudioSettings() {
  const { t } = useTranslation();
  return (
    <div className="space-y-8">
      <div>
        <h3 className="text-xl font-semibold mb-1">{t('settings.audioSubsystem')}</h3>
        <p className="text-sm text-zinc-400">{t('settings.audioSubsystemDesc')}</p>
      </div>

      <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
        <div className="flex items-center justify-between mb-4">
          <h4 className="font-medium">{t('settings.microphone')}</h4>
          <button className="text-xs bg-zinc-800 hover:bg-zinc-700 px-3 py-1.5 rounded-lg transition-colors">
            {t('settings.testMic')}
          </button>
        </div>
        <select className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-emerald-500">
          <option>USB PnP Audio Device (hw:1,0)</option>
          <option>I2S INMP441 (hw:2,0)</option>
        </select>
        <div className="mt-4 h-2 bg-zinc-950 rounded-full overflow-hidden border border-zinc-800">
          <div className="h-full bg-emerald-500 w-1/3 transition-all duration-75" />
        </div>
      </div>
    </div>
  );
}

function SystemSettings() {
  const { t } = useTranslation();
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
            <input type="checkbox" defaultChecked className="rounded border-zinc-700 bg-zinc-950 text-emerald-500 focus:ring-emerald-500 focus:ring-offset-zinc-900" />
            <span className="text-sm text-zinc-300">{t('settings.autoStopAutomation')}</span>
          </label>
          <label className="flex items-center gap-3">
            <input type="checkbox" defaultChecked className="rounded border-zinc-700 bg-zinc-950 text-emerald-500 focus:ring-emerald-500 focus:ring-offset-zinc-900" />
            <span className="text-sm text-zinc-300">{t('settings.stopLlmOnHighTemp')}</span>
          </label>
        </div>
      </div>
    </div>
  );
}
