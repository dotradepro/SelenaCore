import { useTranslation } from 'react-i18next';
import VoskSection from './VoskSection';
import PiperSection from './PiperSection';
import LlmSection from './LlmSection';

export default function VoiceSettings() {
  const { t } = useTranslation();

  return (
    <div className="space-y-8">
      <div>
        <h3 className="text-xl font-semibold mb-1">{t('settings.voiceAssistant')}</h3>
        <p className="text-sm text-zinc-400">{t('settings.voiceAssistantDesc')}</p>
      </div>

      <VoskSection />
      <PiperSection />
      <LlmSection />
    </div>
  );
}
