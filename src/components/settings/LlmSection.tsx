import { useState, useEffect, useCallback } from 'react';
import { RefreshCw, Download, Trash2, Check, Eye, EyeOff, Loader2, Play, Square, MessageSquare, Cloud } from 'lucide-react';
import AccelBadge from './AccelBadge';
import { useTranslation } from 'react-i18next';
import { cn } from '../../lib/utils';

interface Provider {
  id: string;
  name: string;
  needs_key: boolean;
  configured: boolean;
  active: boolean;
  model: string;
}

interface LlmModel {
  id: string;
  name: string;
  size_gb?: number;
  installed?: boolean;
}

export default function LlmSection() {
  const { t } = useTranslation();

  // Provider state
  const [providers, setProviders] = useState<Provider[]>([]);
  const [activeProvider, setActiveProvider] = useState('ollama');

  // Ollama state
  const [ollamaStatus, setOllamaStatus] = useState<{ installed: boolean; version: string | null; running: boolean }>({ installed: false, version: null, running: false });
  const [ollamaInstalling, setOllamaInstalling] = useState(false);
  const [ollamaAction, setOllamaAction] = useState('');
  const [ollamaInstallProgress, setOllamaInstallProgress] = useState('');
  const [ollamaModels, setOllamaModels] = useState<LlmModel[]>([]);
  const [pullModel, setPullModel] = useState('');
  const [pulling, setPulling] = useState(false);
  const [pullProgress, setPullProgress] = useState(0);
  const [pullStatus, setPullStatus] = useState('');
  const [activeModel, setActiveModel] = useState('');

  // Model catalog
  const [catalogModels, setCatalogModels] = useState<LlmModel[]>([]);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [showCatalog, setShowCatalog] = useState(false);

  // llama.cpp state
  const [llamacppRunning, setLlamacppRunning] = useState(false);
  const [llamacppStarting, setLlamacppStarting] = useState(false);

  // Hardware
  const [gpuActive, setGpuActive] = useState(false);

  // System prompt
  const [systemPrompt, setSystemPrompt] = useState('');
  const [defaultPrompt, setDefaultPrompt] = useState('');
  const [isCustomPrompt, setIsCustomPrompt] = useState(false);
  const [savingPrompt, setSavingPrompt] = useState(false);
  const [showPromptEditor, setShowPromptEditor] = useState(false);

  // Cloud state
  const [apiKey, setApiKey] = useState('');
  const [showKey, setShowKey] = useState(false);
  const [validating, setValidating] = useState(false);
  const [keyValid, setKeyValid] = useState<boolean | null>(null);
  const [keyError, setKeyError] = useState('');
  const [cloudModels, setCloudModels] = useState<LlmModel[]>([]);
  const [selectedCloudModel, setSelectedCloudModel] = useState('');
  const [savingProvider, setSavingProvider] = useState(false);

  const fetchProviders = useCallback(async () => {
    try {
      const res = await fetch('/api/ui/setup/llm/providers').then(r => r.json());
      setProviders(res.providers || []);
      setActiveProvider(res.active || 'ollama');
    } catch { /* ignore */ }
  }, []);

  const fetchOllamaStatus = useCallback(async () => {
    try {
      const res = await fetch('/api/ui/setup/ollama/status').then(r => r.json());
      setOllamaStatus(res);
    } catch { /* ignore */ }
  }, []);

  const fetchOllamaModels = useCallback(async () => {
    try {
      const res = await fetch('/api/ui/setup/ollama/models').then(r => r.json());
      setOllamaModels(res.models || []);
    } catch { /* ignore */ }
  }, []);

  const fetchActiveModel = useCallback(async () => {
    try {
      const res = await fetch('/api/ui/setup/llm/models').then(r => r.json());
      setActiveModel(res.active || '');
    } catch { /* ignore */ }
  }, []);

  const fetchSystemPrompt = useCallback(async () => {
    try {
      const res = await fetch('/api/ui/setup/llm/system-prompt').then(r => r.json());
      setSystemPrompt(res.prompt || '');
      setDefaultPrompt(res.default || '');
      setIsCustomPrompt(res.is_custom || false);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    fetchProviders();
    fetchOllamaStatus();
    fetchOllamaModels();
    fetchActiveModel();
    fetchSystemPrompt();
    fetch('/api/ui/setup/hardware/status').then(r => r.json()).then(d => setGpuActive(d.gpu_active || false)).catch(() => {});
    fetchLlamacppStatus();
  }, [fetchProviders, fetchOllamaStatus, fetchOllamaModels, fetchActiveModel, fetchSystemPrompt]);

  // When switching provider tab, load cloud models if configured
  useEffect(() => {
    if (activeProvider !== 'ollama') {
      const prov = providers.find(p => p.id === activeProvider);
      if (prov?.configured) {
        fetchCloudModels(activeProvider);
      }
      setSelectedCloudModel(prov?.model || '');
    }
  }, [activeProvider, providers]);

  const selectProvider = async (providerId: string) => {
    setActiveProvider(providerId);
    try {
      await fetch('/api/ui/setup/llm/provider/select', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider: providerId }),
      });
      fetchProviders();
    } catch { /* ignore */ }
  };

  // Ollama actions
  const handleOllamaInstall = async () => {
    setOllamaInstalling(true);
    setOllamaAction('install');
    setOllamaInstallProgress('downloading ollama...');
    try {
      await fetch('/api/ui/setup/ollama/install', { method: 'POST' });
      const poll = setInterval(async () => {
        const res = await fetch('/api/ui/setup/ollama/install-progress').then(r => r.json());
        if (res.output) setOllamaInstallProgress(res.output.split('\n').filter(Boolean).pop() || '');
        if (!res.running) {
          clearInterval(poll);
          setOllamaInstalling(false);
          setOllamaInstallProgress('');
          fetchOllamaStatus();
        }
      }, 3000);
    } catch { setOllamaInstalling(false); setOllamaInstallProgress(''); }
  };

  const handleOllamaUninstall = async () => {
    setOllamaInstalling(true);
    setOllamaAction('uninstall');
    setOllamaInstallProgress('removing ollama...');
    try {
      await fetch('/api/ui/setup/ollama/uninstall', { method: 'POST' });
      const poll = setInterval(async () => {
        const res = await fetch('/api/ui/setup/ollama/install-progress').then(r => r.json());
        if (res.output) setOllamaInstallProgress(res.output.split('\n').filter(Boolean).pop() || '');
        if (!res.running) {
          clearInterval(poll);
          setOllamaInstalling(false);
          setOllamaInstallProgress('');
          fetchOllamaStatus();
        }
      }, 2000);
    } catch { setOllamaInstalling(false); setOllamaInstallProgress(''); }
  };

  const handleOllamaStart = async () => {
    try {
      await fetch('/api/ui/setup/ollama/start', { method: 'POST' });
      setTimeout(fetchOllamaStatus, 2000);
    } catch { /* ignore */ }
  };

  const handleOllamaStop = async () => {
    try {
      await fetch('/api/ui/setup/ollama/stop', { method: 'POST' });
      setTimeout(fetchOllamaStatus, 1000);
    } catch { /* ignore */ }
  };

  const handlePullModel = async () => {
    if (!pullModel.trim()) return;
    setPulling(true);
    setPullProgress(0);
    setPullStatus('starting...');
    try {
      await fetch('/api/ui/setup/ollama/pull', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: pullModel.trim() }),
      });
      // Poll progress
      const poll = setInterval(async () => {
        try {
          const res = await fetch('/api/ui/setup/ollama/pull-progress').then(r => r.json());
          setPullProgress(res.percent || 0);
          setPullStatus(res.status || '');
          if (!res.running) {
            clearInterval(poll);
            setPulling(false);
            if (res.done) {
              setPullModel('');
              setPullStatus('');
              fetchOllamaModels();
            }
          }
        } catch { /* ignore */ }
      }, 1500);
    } catch {
      setPulling(false);
      setPullStatus('error');
    }
  };

  const deleteOllamaModel = async (modelId: string) => {
    try {
      await fetch('/api/ui/setup/ollama/delete-model', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: modelId }),
      });
      fetchOllamaModels();
    } catch { /* ignore */ }
  };

  const selectOllamaModel = async (modelId: string) => {
    try {
      await fetch('/api/ui/setup/llm/select', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: modelId }),
      });
      setActiveModel(modelId);
    } catch { /* ignore */ }
  };

  // Cloud provider actions
  const validateKey = async () => {
    setValidating(true);
    setKeyValid(null);
    setKeyError('');
    try {
      const res = await fetch('/api/ui/setup/llm/provider/validate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider: activeProvider, api_key: apiKey }),
      }).then(r => r.json());

      setKeyValid(res.valid);
      if (res.valid) {
        setCloudModels(res.models || []);
        // Save the key
        await fetch('/api/ui/setup/llm/provider/apikey', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ provider: activeProvider, api_key: apiKey }),
        });
      } else {
        setKeyError(res.error || t('settings.keyInvalid'));
      }
    } catch {
      setKeyValid(false);
      setKeyError('Connection error');
    }
    setValidating(false);
  };

  const fetchCloudModels = async (provider: string) => {
    try {
      const res = await fetch(`/api/ui/setup/llm/provider/models?provider=${provider}`).then(r => r.json());
      setCloudModels(res.models || []);
    } catch { /* ignore */ }
  };

  const loadCatalog = async () => {
    setShowCatalog(true);
    setCatalogLoading(true);
    try {
      const res = await fetch('/api/ui/setup/llm/catalog').then(r => r.json());
      setCatalogModels(res.models || []);
    } catch { /* ignore */ }
    setCatalogLoading(false);
  };

  const pullFromCatalog = (modelId: string) => {
    setPullModel(modelId);
    setTimeout(() => handlePullModel(), 100);
  };

  // llama.cpp
  const fetchLlamacppStatus = async () => {
    try {
      const res = await fetch('/api/ui/setup/llamacpp/status').then(r => r.json());
      setLlamacppRunning(res.running || false);
    } catch { /* ignore */ }
  };

  const startLlamacpp = async () => {
    setLlamacppStarting(true);
    try {
      await fetch('/api/ui/setup/llamacpp/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      setTimeout(fetchLlamacppStatus, 3000);
    } catch { /* ignore */ }
    setLlamacppStarting(false);
  };

  const stopLlamacpp = async () => {
    try {
      await fetch('/api/ui/setup/llamacpp/stop', { method: 'POST' });
      setTimeout(fetchLlamacppStatus, 1000);
    } catch { /* ignore */ }
  };

  const saveSystemPrompt = async () => {
    setSavingPrompt(true);
    try {
      await fetch('/api/ui/setup/llm/system-prompt', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: systemPrompt }),
      });
      setIsCustomPrompt(systemPrompt !== defaultPrompt);
    } catch { /* ignore */ }
    setSavingPrompt(false);
  };

  const resetSystemPrompt = async () => {
    try {
      const res = await fetch('/api/ui/setup/llm/system-prompt/reset', { method: 'POST' }).then(r => r.json());
      setSystemPrompt(res.prompt || defaultPrompt);
      setIsCustomPrompt(false);
    } catch { /* ignore */ }
  };

  const saveCloudModel = async () => {
    if (!selectedCloudModel) return;
    setSavingProvider(true);
    try {
      await fetch('/api/ui/setup/llm/provider/model', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider: activeProvider, model: selectedCloudModel }),
      });
      fetchProviders();
    } catch { /* ignore */ }
    setSavingProvider(false);
  };

  return (
    <div style={{ background: 'var(--sf)', border: '1px solid var(--b)', borderRadius: 12, padding: 20 }}>
      {/* Header */}
      <div style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <h4 style={{ fontWeight: 600, fontSize: 15, color: 'var(--tx)' }}>{t('settings.llmRouter')}</h4>
          {activeProvider === 'ollama' || activeProvider === 'llamacpp'
            ? <AccelBadge mode={gpuActive ? 'gpu' : 'cpu'} />
            : <span style={{
                display: 'inline-flex', alignItems: 'center', gap: 3,
                fontSize: 10, fontWeight: 500, padding: '2px 6px', borderRadius: 4,
                background: 'rgba(168,85,247,0.12)', color: '#a855f7',
              }}><Cloud size={10} /> Cloud</span>
          }
        </div>
        <p style={{ fontSize: 12, color: 'var(--tx2)', marginTop: 2 }}>{t('settings.llmProviderDesc')}</p>
      </div>

      {/* Provider tabs */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 16, overflowX: 'auto', paddingBottom: 4 }}>
        {providers.map(p => (
          <button key={p.id} onClick={() => selectProvider(p.id)}
            className={cn("text-xs px-3 py-1.5 rounded-lg whitespace-nowrap transition-colors",
              p.id === activeProvider
                ? "bg-blue-600 text-white"
                : "bg-zinc-800 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-700"
            )}>
            {p.name}
            {p.configured && p.id !== 'ollama' && <Check size={10} className="inline ml-1" />}
          </button>
        ))}
      </div>

      {/* System Prompt */}
      <div style={{ marginBottom: 16 }}>
        <button onClick={() => setShowPromptEditor(!showPromptEditor)}
          className="text-xs text-blue-400 hover:text-blue-300 flex items-center gap-1 mb-2">
          <MessageSquare size={12} />
          {t('settings.systemPrompt')}
          {isCustomPrompt && <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-500/10 text-purple-400 ml-1">{t('settings.customPrompt')}</span>}
        </button>

        {showPromptEditor && (
          <div style={{ background: 'var(--sf2)', border: '1px solid var(--b2)', borderRadius: 10, padding: 12 }}>
            <div style={{ fontSize: 11, color: 'var(--tx3)', marginBottom: 6 }}>{t('settings.systemPromptDesc')}</div>
            <textarea
              value={systemPrompt}
              onChange={e => setSystemPrompt(e.target.value)}
              rows={6}
              style={{
                width: '100%', padding: '8px 10px', fontSize: 12, lineHeight: 1.5, resize: 'vertical',
                background: 'var(--sf)', border: '1px solid var(--b)', borderRadius: 8, color: 'var(--tx)',
                fontFamily: 'var(--font-sans)',
              }}
            />
            <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
              <button onClick={saveSystemPrompt} disabled={savingPrompt}
                className="text-xs px-3 py-1.5 rounded-lg bg-emerald-600 text-white hover:bg-emerald-500 disabled:opacity-50 flex items-center gap-1">
                {savingPrompt ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
                {t('common.save')}
              </button>
              <button onClick={resetSystemPrompt}
                className="text-xs px-3 py-1.5 rounded-lg bg-zinc-800 text-zinc-300 hover:bg-zinc-700 flex items-center gap-1">
                <RefreshCw size={12} /> {t('settings.resetToDefault')}
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Ollama panel */}
      {activeProvider === 'ollama' && (
        <div>
          {/* Ollama binary status */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
            <span className={cn("text-xs px-2 py-1 rounded-md font-medium",
              ollamaStatus.installed ? "text-emerald-500 bg-emerald-500/10" : "text-red-400 bg-red-500/10")}>
              {ollamaStatus.installed ? `Ollama v${ollamaStatus.version}` : t('settings.notInstalled')}
            </span>
            {ollamaStatus.installed && (
              <span className={cn("text-xs px-2 py-1 rounded-md font-medium",
                ollamaStatus.running ? "text-emerald-500 bg-emerald-500/10" : "text-amber-400 bg-amber-500/10")}>
                {ollamaStatus.running ? t('settings.serverRunning') : t('settings.serverStopped')}
              </span>
            )}
          </div>

          {/* Ollama actions */}
          <div style={{ display: 'flex', gap: 8, marginBottom: 16, flexWrap: 'wrap' }}>
            {!ollamaStatus.installed ? (
              <button onClick={handleOllamaInstall} disabled={ollamaInstalling}
                className="text-xs px-3 py-1.5 rounded-lg bg-emerald-600 text-white hover:bg-emerald-500 disabled:opacity-50 flex items-center gap-1">
                {ollamaInstalling && ollamaAction === 'install' ? <Loader2 size={12} className="animate-spin" /> : <Download size={12} />}
                {ollamaInstalling && ollamaAction === 'install' ? t('settings.installing') : t('settings.installEngine')}
              </button>
            ) : (
              <>
                {!ollamaStatus.running ? (
                  <button onClick={handleOllamaStart}
                    className="text-xs px-3 py-1.5 rounded-lg bg-emerald-600 text-white hover:bg-emerald-500 flex items-center gap-1">
                    <Play size={12} /> {t('settings.startServer')}
                  </button>
                ) : (
                  <button onClick={handleOllamaStop}
                    className="text-xs px-3 py-1.5 rounded-lg bg-amber-600/20 text-amber-400 hover:bg-amber-600/30 flex items-center gap-1">
                    <Square size={12} /> {t('settings.stopServer')}
                  </button>
                )}
                <button onClick={handleOllamaUninstall} disabled={ollamaInstalling}
                  className="text-xs px-3 py-1.5 rounded-lg bg-red-600/20 text-red-400 hover:bg-red-600/30 disabled:opacity-50 flex items-center gap-1">
                  <Trash2 size={12} /> {t('settings.uninstallEngine')}
                </button>
              </>
            )}
            <button onClick={() => { fetchOllamaStatus(); fetchOllamaModels(); }}
              className="text-xs text-zinc-400 hover:text-zinc-200 flex items-center gap-1">
              <RefreshCw size={12} /> {t('common.refresh')}
            </button>
          </div>

          {/* Install progress */}
          {ollamaInstalling && ollamaInstallProgress && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ fontSize: 11, color: 'var(--tx2)', marginBottom: 4, fontFamily: 'var(--font-mono)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {ollamaInstallProgress}
              </div>
              <div style={{ height: 4, background: 'var(--sf3)', borderRadius: 2, overflow: 'hidden' }}>
                <div className="animate-pulse" style={{ width: '60%', height: '100%', background: 'var(--ac)', borderRadius: 2 }} />
              </div>
            </div>
          )}

          {/* Ollama models */}
          {ollamaModels.length > 0 && (
            <div style={{ marginBottom: 16 }}>
              <div style={{ fontSize: 12, fontWeight: 500, color: 'var(--tx2)', marginBottom: 8 }}>{t('settings.installedModels')}</div>
              <div className="space-y-2">
                {ollamaModels.map(m => (
                  <div key={m.id} className={cn("p-3 rounded-lg border flex items-center justify-between transition-all",
                    m.id === activeModel ? "border-emerald-500 bg-emerald-500/10" : "border-zinc-800 bg-zinc-950")}>
                    <div>
                      <div className="text-sm font-medium">{m.name}</div>
                      {m.size_gb !== undefined && <div className="text-xs text-zinc-500">{m.size_gb} GB</div>}
                    </div>
                    <div className="flex items-center gap-2">
                      <button onClick={() => selectOllamaModel(m.id)} disabled={m.id === activeModel}
                        className={cn("text-xs px-3 py-1.5 rounded-lg transition-colors",
                          m.id === activeModel ? "bg-emerald-500/20 text-emerald-500" : "bg-zinc-800 text-zinc-300 hover:bg-zinc-700")}>
                        {m.id === activeModel ? <Check size={12} /> : t('settings.activate')}
                      </button>
                      <button onClick={() => deleteOllamaModel(m.id)} disabled={m.id === activeModel}
                        className="text-xs px-2 py-1.5 rounded-lg text-red-400 hover:bg-red-500/10 disabled:opacity-30">
                        <Trash2 size={12} />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Pull new model */}
          {ollamaStatus.running && (
            <div>
              <div style={{ display: 'flex', gap: 8 }}>
                <input
                  type="text" value={pullModel} onChange={e => setPullModel(e.target.value)}
                  placeholder={t('settings.modelNamePlaceholder')}
                  onKeyDown={e => e.key === 'Enter' && handlePullModel()}
                  disabled={pulling}
                  style={{
                    flex: 1, padding: '6px 10px', fontSize: 12,
                    background: 'var(--sf2)', border: '1px solid var(--b2)', borderRadius: 8, color: 'var(--tx)',
                  }}
                />
                <button onClick={handlePullModel} disabled={pulling || !pullModel.trim()}
                  className="text-xs px-3 py-1.5 rounded-lg bg-zinc-800 text-zinc-300 hover:bg-zinc-700 disabled:opacity-50 flex items-center gap-1">
                  {pulling ? <Loader2 size={12} className="animate-spin" /> : <Download size={12} />}
                  Pull
                </button>
              </div>
              {pulling && (
                <div style={{ marginTop: 8 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--tx2)', marginBottom: 4 }}>
                    <span>{pullStatus}</span>
                    <span>{pullProgress.toFixed(1)}%</span>
                  </div>
                  <div style={{ height: 6, background: 'var(--sf3)', borderRadius: 3, overflow: 'hidden' }}>
                    <div style={{
                      width: `${pullProgress}%`, height: '100%',
                      background: 'var(--ac)', borderRadius: 3, transition: 'width 0.3s',
                    }} />
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Model catalog (for Ollama) */}
      {activeProvider === 'ollama' && (
        <div style={{ marginTop: 12 }}>
          <button onClick={showCatalog ? () => setShowCatalog(false) : loadCatalog}
            className="text-xs text-blue-400 hover:text-blue-300 mb-2">
            {showCatalog ? t('settings.hideCatalog') : t('settings.showCatalog')}
          </button>
          {showCatalog && (
            <div>
              {catalogLoading ? (
                <div className="flex items-center gap-2 text-xs text-zinc-400 py-2">
                  <Loader2 size={14} className="animate-spin" /> {t('settings.fetchingCatalog')}
                </div>
              ) : catalogModels.length === 0 ? (
                <div className="text-xs text-zinc-500 py-2">{t('settings.noModelsFound')}</div>
              ) : (
                <div className="space-y-1" style={{ maxHeight: 250, overflowY: 'auto' }}>
                  {catalogModels.map(m => (
                    <div key={m.id} className="p-2 rounded-lg border border-zinc-800 bg-zinc-950 flex items-center justify-between text-xs">
                      <div>
                        <span className="font-medium">{m.name}</span>
                        <span className="text-zinc-500 ml-2">{m.size_gb} GB</span>
                      </div>
                      <button onClick={() => pullFromCatalog(m.id)} disabled={pulling}
                        className="px-2 py-1 rounded bg-zinc-800 text-zinc-300 hover:bg-zinc-700 disabled:opacity-50 flex items-center gap-1">
                        <Download size={10} /> Pull
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* llama.cpp panel */}
      {activeProvider === 'llamacpp' && (
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
            <span className={cn("text-xs px-2 py-1 rounded-md font-medium",
              llamacppRunning ? "text-emerald-500 bg-emerald-500/10" : "text-amber-400 bg-amber-500/10")}>
              {llamacppRunning ? t('settings.serverRunning') : t('settings.serverStopped')}
            </span>
          </div>

          <div style={{ fontSize: 11, color: 'var(--tx3)', marginBottom: 10 }}>
            {t('settings.llamacppDesc')}
          </div>

          {/* Select model from installed Ollama models */}
          {ollamaModels.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <label style={{ display: 'block', fontSize: 12, fontWeight: 500, color: 'var(--tx2)', marginBottom: 6 }}>
                {t('settings.selectModel')}
              </label>
              <select
                value={providers.find(p => p.id === 'llamacpp')?.model || ''}
                onChange={async (e) => {
                  await fetch('/api/ui/setup/llm/provider/model', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ provider: 'llamacpp', model: e.target.value }),
                  });
                  fetchProviders();
                }}
                style={{
                  width: '100%', padding: '8px 10px', fontSize: 12,
                  background: 'var(--sf2)', border: '1px solid var(--b2)', borderRadius: 8, color: 'var(--tx)',
                }}>
                <option value="">-- {t('settings.selectModel')} --</option>
                {ollamaModels.map(m => (
                  <option key={m.id} value={m.id}>{m.name} ({m.size_gb} GB)</option>
                ))}
              </select>
            </div>
          )}

          {ollamaModels.length === 0 && (
            <div style={{ fontSize: 11, color: 'var(--rd)', marginBottom: 12 }}>
              {t('settings.llamacppNoModels')}
            </div>
          )}

          {/* Start / Stop */}
          <div style={{ display: 'flex', gap: 8, marginBottom: 16, flexWrap: 'wrap' }}>
            {!llamacppRunning ? (
              <button onClick={startLlamacpp}
                disabled={llamacppStarting || !providers.find(p => p.id === 'llamacpp')?.model}
                className="text-xs px-3 py-1.5 rounded-lg bg-emerald-600 text-white hover:bg-emerald-500 disabled:opacity-50 flex items-center gap-1">
                {llamacppStarting ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
                {t('settings.startServer')}
              </button>
            ) : (
              <button onClick={stopLlamacpp}
                className="text-xs px-3 py-1.5 rounded-lg bg-amber-600/20 text-amber-400 hover:bg-amber-600/30 flex items-center gap-1">
                <Square size={12} /> {t('settings.stopServer')}
              </button>
            )}
            <button onClick={() => { fetchLlamacppStatus(); fetchOllamaModels(); }}
              className="text-xs text-zinc-400 hover:text-zinc-200 flex items-center gap-1">
              <RefreshCw size={12} /> {t('common.refresh')}
            </button>
          </div>
        </div>
      )}

      {/* Cloud provider panel */}
      {activeProvider !== 'ollama' && activeProvider !== 'llamacpp' && (
        <div>
          {/* API key input */}
          <div style={{ marginBottom: 16 }}>
            <label style={{ display: 'block', fontSize: 12, fontWeight: 500, color: 'var(--tx2)', marginBottom: 6 }}>
              {t('settings.apiKey')}
            </label>
            <div style={{ display: 'flex', gap: 8 }}>
              <div style={{ position: 'relative', flex: 1 }}>
                <input
                  type={showKey ? 'text' : 'password'}
                  value={apiKey}
                  onChange={e => { setApiKey(e.target.value); setKeyValid(null); }}
                  placeholder={t('settings.apiKeyPlaceholder')}
                  style={{
                    width: '100%', padding: '8px 36px 8px 10px', fontSize: 12,
                    background: 'var(--sf2)', border: `1px solid ${keyValid === true ? 'var(--gr)' : keyValid === false ? 'var(--rd)' : 'var(--b2)'}`,
                    borderRadius: 8, color: 'var(--tx)',
                  }}
                />
                <button onClick={() => setShowKey(!showKey)}
                  style={{ position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--tx3)' }}>
                  {showKey ? <EyeOff size={14} /> : <Eye size={14} />}
                </button>
              </div>
              <button onClick={validateKey} disabled={validating || !apiKey.trim()}
                className={cn("text-xs px-4 py-2 rounded-lg font-medium disabled:opacity-50 flex items-center gap-1",
                  keyValid === true ? "bg-emerald-600 text-white" : "bg-blue-600 text-white hover:bg-blue-500")}>
                {validating ? <Loader2 size={12} className="animate-spin" /> :
                  keyValid === true ? <Check size={12} /> : null}
                {validating ? t('settings.validatingKey') :
                  keyValid === true ? t('settings.keyValid') : t('settings.validateKey')}
              </button>
            </div>
            {keyError && <div style={{ fontSize: 11, color: 'var(--rd)', marginTop: 4 }}>{keyError}</div>}
          </div>

          {/* Model selector */}
          {cloudModels.length > 0 && (
            <div style={{ marginBottom: 16 }}>
              <label style={{ display: 'block', fontSize: 12, fontWeight: 500, color: 'var(--tx2)', marginBottom: 6 }}>
                {t('settings.selectModel')}
              </label>
              <div style={{ display: 'flex', gap: 8 }}>
                <select value={selectedCloudModel} onChange={e => setSelectedCloudModel(e.target.value)}
                  style={{
                    flex: 1, padding: '8px 10px', fontSize: 12,
                    background: 'var(--sf2)', border: '1px solid var(--b2)', borderRadius: 8, color: 'var(--tx)',
                  }}>
                  <option value="">-- {t('settings.selectModel')} --</option>
                  {cloudModels.map(m => (
                    <option key={m.id} value={m.id}>{m.name || m.id}</option>
                  ))}
                </select>
                <button onClick={saveCloudModel} disabled={!selectedCloudModel || savingProvider}
                  className="text-xs px-4 py-2 rounded-lg bg-emerald-600 text-white hover:bg-emerald-500 disabled:opacity-50 flex items-center gap-1">
                  {savingProvider ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
                  {t('common.save')}
                </button>
              </div>
            </div>
          )}

          {/* Loading models indicator */}
          {keyValid === true && cloudModels.length === 0 && (
            <div className="flex items-center gap-2 text-xs text-zinc-400 py-2">
              <Loader2 size={14} className="animate-spin" /> {t('settings.cloudModelsLoading')}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
