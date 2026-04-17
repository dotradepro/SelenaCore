import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { cn } from '../lib/utils';

type ProviderId = '' | 'skip' | 'ollama' | 'openai' | 'anthropic' | 'groq' | 'google';

interface LlmFormSlice {
  llmProvider: ProviderId;
  llmUrl: string;
  llmApiKey: string;
  llmModel: string;
}

interface Props {
  formData: LlmFormSlice;
  setFormData: (updater: (prev: any) => any) => void;
}

interface Model {
  id: string;
  name: string;
}

type TestState =
  | { kind: 'idle' }
  | { kind: 'testing' }
  | { kind: 'ok'; models: Model[] }
  | { kind: 'authRequired' }
  | { kind: 'error'; message: string };

const CLOUD_PROVIDERS: ProviderId[] = ['openai', 'anthropic', 'groq', 'google'];

/**
 * Wizard step: pick an LLM provider (local Ollama or cloud) and save the
 * connection. Skip is first-class — on skip we write nothing to
 * voice.llm_provider so the runtime treats it as "no LLM configured" and
 * novel phrases silently fall through to the deterministic fallback.
 */
export function LlmProviderStep({ formData, setFormData }: Props) {
  const { t } = useTranslation();
  const [test, setTest] = useState<TestState>({ kind: 'idle' });

  const update = (patch: Partial<LlmFormSlice>) => {
    setFormData((prev: any) => ({ ...prev, ...patch }));
    setTest({ kind: 'idle' });
  };

  const isCloud = CLOUD_PROVIDERS.includes(formData.llmProvider);
  const isOllama = formData.llmProvider === 'ollama';
  const isSkip = formData.llmProvider === 'skip' || formData.llmProvider === '';

  async function runTest() {
    if (isSkip) return;
    setTest({ kind: 'testing' });
    try {
      const body: Record<string, string> = { provider: formData.llmProvider };
      if (isOllama) {
        body.url = formData.llmUrl || 'http://localhost:11434';
        if (formData.llmApiKey) body.api_key = formData.llmApiKey;
      } else if (formData.llmApiKey) {
        body.api_key = formData.llmApiKey;
      }
      const resp = await fetch('/api/ui/setup/llm/provider/validate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await resp.json();
      if (data?.valid) {
        // For cloud, re-fetch with text_only filter. For Ollama, validate
        // already returns the full tag list — still sanitise to {id,name}.
        let models: Model[] = Array.isArray(data.models)
          ? data.models.map((m: any) => ({
              id: String(m.id ?? ''),
              name: String(m.name ?? m.id ?? ''),
            })).filter((m: Model) => m.id)
          : [];
        if (isCloud) {
          try {
            const r2 = await fetch(
              `/api/ui/setup/llm/provider/models?provider=${formData.llmProvider}&text_only=1`,
            );
            const d2 = await r2.json();
            if (Array.isArray(d2?.models)) {
              models = d2.models.map((m: any) => ({
                id: String(m.id ?? ''),
                name: String(m.name ?? m.id ?? ''),
              })).filter((m: Model) => m.id);
            }
          } catch { /* keep validate models as fallback */ }
        }
        setTest({ kind: 'ok', models });
        if (models.length > 0 && !formData.llmModel) {
          update({ llmModel: models[0].id });
        }
      } else if (data?.auth_required) {
        setTest({ kind: 'authRequired' });
      } else {
        setTest({ kind: 'error', message: data?.error || t('wizard.llmUnreachable') });
      }
    } catch (e: any) {
      setTest({ kind: 'error', message: e?.message || 'network error' });
    }
  }

  return (
    <div className="space-y-3">
      <h2 className="text-base font-medium">{t('wizard.llmTitle')}</h2>
      <p className="text-zinc-400 text-xs">{t('wizard.llmDesc')}</p>

      <div>
        <label className="block text-xs font-medium text-zinc-400 mb-1">
          {t('wizard.llmProviderLabel')}
        </label>
        <select
          value={formData.llmProvider}
          onChange={(e) => update({ llmProvider: e.target.value as ProviderId, llmModel: '' })}
          className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-zinc-50 focus:outline-none focus:border-emerald-500"
        >
          <option value="">{t('wizard.llmChoose')}</option>
          <option value="skip">{t('wizard.llmSkip')}</option>
          <option value="ollama">{t('wizard.llmOllama')}</option>
          <option value="openai">OpenAI</option>
          <option value="anthropic">Anthropic</option>
          <option value="groq">Groq</option>
          <option value="google">Google</option>
        </select>
      </div>

      {isSkip && formData.llmProvider === 'skip' && (
        <div className="text-[11px] text-amber-400/80 bg-amber-500/10 border border-amber-500/20 rounded-lg px-3 py-2">
          {t('wizard.llmSkipWarning')}
        </div>
      )}

      {isOllama && (
        <>
          <div>
            <label className="block text-xs font-medium text-zinc-400 mb-1">
              {t('wizard.llmOllamaUrl')}
            </label>
            <input
              type="text"
              value={formData.llmUrl}
              onChange={(e) => update({ llmUrl: e.target.value })}
              placeholder="http://localhost:11434"
              className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-zinc-50 focus:outline-none focus:border-emerald-500 font-mono"
            />
            <p className="mt-1 text-[11px] text-zinc-500">{t('wizard.llmOllamaUrlHint')}</p>
          </div>
          <div>
            <label className="block text-xs font-medium text-zinc-400 mb-1">
              {t('wizard.llmApiKeyOptional')}
            </label>
            <input
              type="password"
              value={formData.llmApiKey}
              onChange={(e) => update({ llmApiKey: e.target.value })}
              placeholder={t('wizard.llmOllamaKeyPlaceholder')}
              className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-zinc-50 focus:outline-none focus:border-emerald-500"
            />
            <p className="mt-1 text-[11px] text-zinc-500">{t('wizard.llmOllamaKeyHint')}</p>
          </div>
        </>
      )}

      {isCloud && (
        <div>
          <label className="block text-xs font-medium text-zinc-400 mb-1">
            {t('wizard.llmApiKey')}
          </label>
          <input
            type="password"
            value={formData.llmApiKey}
            onChange={(e) => update({ llmApiKey: e.target.value })}
            placeholder={t('wizard.llmApiKeyPlaceholder')}
            className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-zinc-50 focus:outline-none focus:border-emerald-500"
          />
        </div>
      )}

      {!isSkip && (
        <>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={runTest}
              disabled={test.kind === 'testing'}
              className={cn(
                "px-3 py-1.5 text-xs rounded border transition-colors",
                "bg-emerald-500/20 border-emerald-500 text-emerald-300",
                "disabled:opacity-50",
              )}
            >
              {test.kind === 'testing' ? t('wizard.llmTesting') : t('wizard.llmTest')}
            </button>
            {test.kind === 'ok' && (
              <span className="text-[11px] text-emerald-400">✓ {t('wizard.llmReachable')}</span>
            )}
            {test.kind === 'authRequired' && (
              <span className="text-[11px] text-amber-400">⚠ {t('wizard.llmAuthRequired')}</span>
            )}
            {test.kind === 'error' && (
              <span className="text-[11px] text-red-400">✗ {t('wizard.llmUnreachable')}</span>
            )}
          </div>

          {test.kind === 'authRequired' && (
            <div className="text-[11px] text-amber-400/80 bg-amber-500/10 border border-amber-500/20 rounded-lg px-3 py-2">
              {t('wizard.llmAuthRequiredHint')}
            </div>
          )}

          {test.kind === 'ok' && test.models.length > 0 && (
            <div>
              <label className="block text-xs font-medium text-zinc-400 mb-1">
                {t('wizard.llmModel')}
              </label>
              <select
                value={formData.llmModel}
                onChange={(e) => update({ llmModel: e.target.value })}
                className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-zinc-50 focus:outline-none focus:border-emerald-500"
              >
                {test.models.map((m) => (
                  <option key={m.id} value={m.id}>{m.name}</option>
                ))}
              </select>
            </div>
          )}

          {test.kind === 'ok' && isOllama && test.models.length === 0 && (
            <div className="text-[11px] text-zinc-400 bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2">
              {t('wizard.llmNoModels')}{' '}
              <code className="font-mono text-zinc-300">ollama pull llama3.2</code>
            </div>
          )}
        </>
      )}
    </div>
  );
}
