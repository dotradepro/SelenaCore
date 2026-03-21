import { useEffect, useState } from 'react';
import { Search, Download, Trash2, Play, Square } from 'lucide-react';
import { useStore } from '../store/useStore';
import { cn } from '../lib/utils';

export default function Modules() {
  const modules = useStore((state) => state.modules);
  const modulesLoading = useStore((state) => state.modulesLoading);
  const fetchModules = useStore((state) => state.fetchModules);
  const stopModule = useStore((state) => state.stopModule);
  const startModule = useStore((state) => state.startModule);
  const removeModule = useStore((state) => state.removeModule);
  const [search, setSearch] = useState('');

  const filtered = modules.filter((m) =>
    m.name.toLowerCase().includes(search.toLowerCase())
  );

  useEffect(() => {
    fetchModules();
  }, [fetchModules]);

  return (
    <div className="max-w-6xl mx-auto space-y-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">Модули</h1>
          <p className="text-zinc-400 mt-1">Управление плагинами и интеграциями (Plugin Manager).</p>
        </div>
        <button className="bg-emerald-500 hover:bg-emerald-400 text-zinc-950 px-4 py-2 rounded-lg font-medium text-sm transition-colors flex items-center gap-2">
          <Download size={16} />
          Маркетплейс
        </button>
      </div>

      <div className="bg-zinc-900/50 border border-zinc-800 rounded-2xl overflow-hidden">
        <div className="p-4 border-b border-zinc-800 flex items-center gap-4">
          <div className="relative flex-1 max-w-md">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" size={16} />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Поиск модулей..."
              className="w-full bg-zinc-950 border border-zinc-800 rounded-lg pl-9 pr-4 py-2 text-sm text-zinc-50 focus:outline-none focus:border-emerald-500 transition-colors"
            />
          </div>
        </div>

        <div className="divide-y divide-zinc-800">
          {modulesLoading && filtered.length === 0 && (
            <div className="p-8 text-center text-zinc-500 text-sm">Загрузка...</div>
          )}
          {!modulesLoading && filtered.length === 0 && (
            <div className="p-8 text-center text-zinc-500 text-sm">Нет установленных модулей.</div>
          )}
          {filtered.map((mod) => {
            const running = mod.status.toUpperCase() === 'RUNNING';
            return (
              <div key={mod.name} className="p-4 flex items-center justify-between hover:bg-zinc-800/20 transition-colors">
                <div className="flex items-center gap-4">
                  <div className={cn(
                    "w-10 h-10 rounded-xl flex items-center justify-center font-medium text-sm",
                    mod.type === 'SYSTEM' ? "bg-indigo-500/20 text-indigo-400" : "bg-zinc-800 text-zinc-300"
                  )}>
                    {mod.name[0].toUpperCase()}
                  </div>
                  <div>
                    <div className="font-medium flex items-center gap-2">
                      {mod.name}
                      <span className="text-xs px-2 py-0.5 rounded-md bg-zinc-800 text-zinc-400 border border-zinc-700">
                        v{mod.version}
                      </span>
                    </div>
                    <div className="text-xs text-zinc-500 mt-1 flex items-center gap-3">
                      <span>{mod.type}</span>
                      <span className="w-1 h-1 rounded-full bg-zinc-700" />
                      <span>:{mod.port}</span>
                    </div>
                  </div>
                </div>

                <div className="flex items-center gap-3">
                  <div className="flex items-center gap-2 mr-4">
                    <div className={cn('w-2 h-2 rounded-full', running ? 'bg-emerald-500' : 'bg-zinc-600')} />
                    <span className="text-sm text-zinc-400">{running ? 'Работает' : mod.status}</span>
                  </div>

                  {running ? (
                    <button
                      onClick={() => stopModule(mod.name)}
                      className="p-2 text-zinc-400 hover:text-amber-400 hover:bg-zinc-800 rounded-lg transition-colors"
                      title="Остановить"
                    >
                      <Square size={18} />
                    </button>
                  ) : (
                    <button
                      onClick={() => startModule(mod.name)}
                      className="p-2 text-zinc-400 hover:text-emerald-400 hover:bg-zinc-800 rounded-lg transition-colors"
                      title="Запустить"
                    >
                      <Play size={18} />
                    </button>
                  )}

                  <button
                    onClick={() => removeModule(mod.name)}
                    className="p-2 text-zinc-400 hover:text-red-400 hover:bg-zinc-800 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                    disabled={mod.type === 'SYSTEM'}
                    title={mod.type === 'SYSTEM' ? 'Системный модуль нельзя удалить' : 'Удалить'}
                  >
                    <Trash2 size={18} />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
