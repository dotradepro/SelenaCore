import { useEffect } from 'react';
import { Activity, Cpu, HardDrive, Thermometer, Power, ToggleLeft, ToggleRight } from 'lucide-react';
import { useStore } from '../store/useStore';
import { cn } from '../lib/utils';
import type { Device } from '../store/useStore';

function deviceIsOn(device: Device): boolean {
  const s = device.state as Record<string, unknown>;
  if (s.on !== undefined) return Boolean(s.on);
  if (s.power !== undefined) return Boolean(s.power);
  if (s.active !== undefined) return Boolean(s.active);
  if (s.state !== undefined) return s.state === 'on' || s.state === true;
  return false;
}

function formatUptime(seconds: number): string {
  if (!seconds) return '—';
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}д ${h}ч`;
  if (h > 0) return `${h}ч ${m}м`;
  return `${m}м`;
}

export default function Dashboard() {
  const stats = useStore((s) => s.stats);
  const modules = useStore((s) => s.modules);
  const devices = useStore((s) => s.devices);
  const fetchStats = useStore((s) => s.fetchStats);
  const fetchModules = useStore((s) => s.fetchModules);
  const fetchDevices = useStore((s) => s.fetchDevices);
  const updateDeviceState = useStore((s) => s.updateDeviceState);

  useEffect(() => {
    fetchStats();
    fetchModules();
    fetchDevices();
    const interval = setInterval(fetchStats, 5000);
    return () => clearInterval(interval);
  }, [fetchStats, fetchModules, fetchDevices]);

  const cpuTemp = stats?.cpuTemp ?? 0;
  const ramUsed = stats?.ramUsedMb ?? 0;
  const ramTotal = stats?.ramTotalMb ?? 1;
  const diskUsed = stats?.diskUsedGb ?? 0;
  const diskTotal = stats?.diskTotalGb ?? 1;
  const ramPct = ramTotal > 0 ? Math.round((ramUsed / ramTotal) * 100) : 0;
  const diskPct = diskTotal > 0 ? Math.round((diskUsed / diskTotal) * 100) : 0;

  const actuators = devices.filter((d) =>
    d.type === 'actuator' || d.type === 'virtual'
  ).slice(0, 4);

  return (
    <div className="space-y-8 max-w-6xl mx-auto">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight">Добро пожаловать домой</h1>
        <p className="text-zinc-400 mt-1">
          {stats?.mode === 'safe_mode'
            ? '⚠ Система в безопасном режиме'
            : 'Все системы работают в штатном режиме.'}
        </p>
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
              {stats?.version ?? 'v0.3-beta'}
            </span>
          </div>

          <div className="space-y-5 flex-1">
            <div>
              <div className="flex justify-between text-sm mb-2">
                <span className="text-zinc-400 flex items-center gap-2">
                  <Thermometer size={14} /> CPU Temp
                </span>
                <span className={cn('font-mono', cpuTemp > 80 ? 'text-red-400' : 'text-zinc-50')}>
                  {cpuTemp > 0 ? `${cpuTemp.toFixed(1)}°C` : '—'}
                </span>
              </div>
              <div className="h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                <div
                  className={cn('h-full rounded-full', cpuTemp > 80 ? 'bg-red-500' : 'bg-emerald-500')}
                  style={{ width: `${Math.min((cpuTemp / 100) * 100, 100)}%` }}
                />
              </div>
            </div>

            <div>
              <div className="flex justify-between text-sm mb-2">
                <span className="text-zinc-400 flex items-center gap-2">
                  <Cpu size={14} /> RAM
                </span>
                <span className={cn('font-mono', ramPct > 85 ? 'text-red-400' : 'text-zinc-50')}>
                  {ramUsed} / {ramTotal} MB
                </span>
              </div>
              <div className="h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                <div
                  className={cn('h-full rounded-full', ramPct > 85 ? 'bg-red-500' : 'bg-indigo-500')}
                  style={{ width: `${ramPct}%` }}
                />
              </div>
            </div>

            <div>
              <div className="flex justify-between text-sm mb-2">
                <span className="text-zinc-400 flex items-center gap-2">
                  <HardDrive size={14} /> Диск
                </span>
                <span className="font-mono text-zinc-50">
                  {diskUsed.toFixed(1)} / {diskTotal.toFixed(1)} GB
                </span>
              </div>
              <div className="h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                <div
                  className={cn('h-full rounded-full', diskPct > 85 ? 'bg-red-500' : 'bg-zinc-500')}
                  style={{ width: `${diskPct}%` }}
                />
              </div>
            </div>

            <div className="pt-2 border-t border-zinc-800 text-xs text-zinc-500 flex justify-between">
              <span>Uptime: {formatUptime(stats?.uptime ?? 0)}</span>
              <span
                className={cn(
                  stats?.integrity === 'ok' ? 'text-emerald-500' : 'text-red-400'
                )}
              >
                Integrity: {stats?.integrity ?? '—'}
              </span>
            </div>
          </div>
        </div>

        {/* Quick Actions — real actuator devices */}
        <div className="col-span-1 md:col-span-2">
          <h2 className="font-medium mb-4">Быстрые действия</h2>
          {actuators.length === 0 ? (
            <div className="rounded-2xl border border-zinc-800 bg-zinc-900/30 p-8 text-center text-zinc-500 text-sm">
              Нет устройств типа actuator / virtual.
              <br />
              Добавьте устройства через Core API.
            </div>
          ) : (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
              {actuators.map((device) => {
                const on = deviceIsOn(device);
                return (
                  <button
                    key={device.device_id}
                    onClick={() =>
                      updateDeviceState(device.device_id, { on: !on })
                    }
                    className={cn(
                      'p-4 rounded-2xl border flex flex-col items-start gap-4 transition-all text-left',
                      on
                        ? 'bg-zinc-800 border-zinc-700'
                        : 'bg-zinc-900/50 border-zinc-800 hover:bg-zinc-800/50'
                    )}
                  >
                    <div
                      className={cn(
                        'w-10 h-10 rounded-full flex items-center justify-center',
                        on
                          ? 'bg-emerald-500/20 text-emerald-400'
                          : 'bg-zinc-800 text-zinc-400'
                      )}
                    >
                      {on ? <ToggleRight size={20} /> : <ToggleLeft size={20} />}
                    </div>
                    <div>
                      <div className="font-medium text-sm truncate max-w-[90px]">
                        {device.name}
                      </div>
                      <div className="text-xs text-zinc-500 mt-0.5">
                        {on ? 'Включено' : 'Выключено'}
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-3 gap-4">
        {[
          { label: 'Устройств', value: devices.length, icon: Power },
          { label: 'Модулей', value: modules.length, icon: Activity },
          {
            label: 'Активных',
            value: modules.filter(
              (m) => m.status.toUpperCase() === 'RUNNING'
            ).length,
            icon: Activity,
          },
        ].map(({ label, value, icon: Icon }) => (
          <div
            key={label}
            className="bg-zinc-900/50 border border-zinc-800 rounded-2xl p-5 flex items-center gap-4"
          >
            <div className="w-10 h-10 rounded-xl bg-zinc-800 flex items-center justify-center text-zinc-400">
              <Icon size={18} />
            </div>
            <div>
              <div className="text-2xl font-semibold">{value}</div>
              <div className="text-xs text-zinc-500">{label}</div>
            </div>
          </div>
        ))}
      </div>

      {/* Active Modules */}
      <div>
        <h2 className="font-medium mb-4">Активные модули</h2>
        {modules.length === 0 ? (
          <div className="rounded-xl border border-zinc-800 bg-zinc-900/30 p-8 text-center text-zinc-500 text-sm">
            Нет установленных модулей.
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {modules.map((mod) => (
              <div
                key={mod.name}
                className="p-4 rounded-xl border border-zinc-800 bg-zinc-900/30 flex items-center justify-between"
              >
                <div>
                  <div className="font-medium text-sm flex items-center gap-2">
                    <div
                      className={cn(
                        'w-2 h-2 rounded-full',
                        mod.status.toUpperCase() === 'RUNNING'
                          ? 'bg-emerald-500'
                          : 'bg-zinc-600'
                      )}
                    />
                    {mod.name}
                  </div>
                  <div className="text-xs text-zinc-500 mt-1">{mod.type}</div>
                </div>
                <div className="text-xs font-mono text-zinc-400 bg-zinc-950 px-2 py-1 rounded-md border border-zinc-800">
                  :{mod.port}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
