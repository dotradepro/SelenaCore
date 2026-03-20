import { useEffect } from 'react';
import { Activity, Cpu, HardDrive, Thermometer, Lightbulb, Lock, Fan, Power } from 'lucide-react';
import { useStore } from '../store/useStore';
import { cn } from '../lib/utils';

export default function Dashboard() {
  const stats = useStore((state) => state.systemStats);
  const modules = useStore((state) => state.modules);
  const fetchStats = useStore((state) => state.fetchStats);
  const fetchModules = useStore((state) => state.fetchModules);

  useEffect(() => {
    fetchModules();
    fetchStats();
    const interval = setInterval(() => {
      fetchStats();
    }, 2000);
    return () => clearInterval(interval);
  }, [fetchStats, fetchModules]);

  const quickActions = [
    { id: 1, name: 'Свет гостиная', icon: Lightbulb, state: true, type: 'light' },
    { id: 2, name: 'Свет кухня', icon: Lightbulb, state: false, type: 'light' },
    { id: 3, name: 'Замок вход', icon: Lock, state: true, type: 'lock' },
    { id: 4, name: 'Кондиционер', icon: Fan, state: false, type: 'climate' },
  ];

  return (
    <div className="space-y-8 max-w-6xl mx-auto">
      {/* Welcome Section */}
      <div>
        <h1 className="text-3xl font-semibold tracking-tight">Добро пожаловать домой</h1>
        <p className="text-zinc-400 mt-1">Все системы работают в штатном режиме.</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        {/* System Monitor Widget */}
        <div className="col-span-1 bg-zinc-900/50 border border-zinc-800 rounded-2xl p-6 flex flex-col">
          <div className="flex items-center justify-between mb-6">
            <h2 className="font-medium flex items-center gap-2">
              <Activity size={18} className="text-emerald-500" />
              Ядро системы
            </h2>
            <span className="text-xs px-2 py-1 rounded-full bg-emerald-500/10 text-emerald-500 font-medium">
              v0.3-beta
            </span>
          </div>
          
          <div className="space-y-5 flex-1">
            <div>
              <div className="flex justify-between text-sm mb-2">
                <span className="text-zinc-400 flex items-center gap-2"><Thermometer size={14}/> CPU Temp</span>
                <span className={cn("font-mono", stats.cpuTemp > 80 ? "text-red-400" : "text-zinc-50")}>
                  {stats.cpuTemp}°C
                </span>
              </div>
              <div className="h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                <div 
                  className={cn("h-full rounded-full", stats.cpuTemp > 80 ? "bg-red-500" : "bg-emerald-500")} 
                  style={{ width: `${(stats.cpuTemp / 100) * 100}%` }} 
                />
              </div>
            </div>

            <div>
              <div className="flex justify-between text-sm mb-2">
                <span className="text-zinc-400 flex items-center gap-2"><Cpu size={14}/> RAM Free</span>
                <span className={cn("font-mono", stats.ramFree < 150 ? "text-red-400" : "text-zinc-50")}>
                  {stats.ramFree} MB
                </span>
              </div>
              <div className="h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                <div 
                  className={cn("h-full rounded-full", stats.ramFree < 150 ? "bg-red-500" : "bg-indigo-500")} 
                  style={{ width: `${(stats.ramFree / 4096) * 100}%` }} 
                />
              </div>
            </div>

            <div>
              <div className="flex justify-between text-sm mb-2">
                <span className="text-zinc-400 flex items-center gap-2"><HardDrive size={14}/> Disk Free</span>
                <span className="font-mono text-zinc-50">
                  {(stats.diskFree / 1024).toFixed(1)} GB
                </span>
              </div>
              <div className="h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                <div className="h-full bg-zinc-500 rounded-full" style={{ width: '60%' }} />
              </div>
            </div>
          </div>
        </div>

        {/* Quick Actions */}
        <div className="col-span-1 md:col-span-2">
          <h2 className="font-medium mb-4">Быстрые действия</h2>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            {quickActions.map((action) => (
              <button
                key={action.id}
                className={cn(
                  "p-4 rounded-2xl border flex flex-col items-start gap-4 transition-all text-left",
                  action.state 
                    ? "bg-zinc-800 border-zinc-700" 
                    : "bg-zinc-900/50 border-zinc-800 hover:bg-zinc-800/50"
                )}
              >
                <div className={cn(
                  "w-10 h-10 rounded-full flex items-center justify-center",
                  action.state 
                    ? "bg-emerald-500/20 text-emerald-400" 
                    : "bg-zinc-800 text-zinc-400"
                )}>
                  <action.icon size={20} />
                </div>
                <div>
                  <div className="font-medium text-sm">{action.name}</div>
                  <div className="text-xs text-zinc-500 mt-0.5">
                    {action.state ? 'Включено' : 'Выключено'}
                  </div>
                </div>
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Active Modules */}
      <div>
        <h2 className="font-medium mb-4">Активные модули (Sandbox)</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {modules.map((mod) => (
            <div key={mod.name} className="p-4 rounded-xl border border-zinc-800 bg-zinc-900/30 flex items-center justify-between">
              <div>
                <div className="font-medium text-sm flex items-center gap-2">
                  <div className={cn("w-2 h-2 rounded-full", mod.status === 'running' ? "bg-emerald-500" : "bg-zinc-600")} />
                  {mod.name}
                </div>
                <div className="text-xs text-zinc-500 mt-1">{mod.type}</div>
              </div>
              <div className="text-xs font-mono text-zinc-400 bg-zinc-950 px-2 py-1 rounded-md border border-zinc-800">
                {mod.size}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
