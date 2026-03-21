import { useEffect, useState } from 'react';
import { Search, RefreshCw, Thermometer, Zap, Cpu, Radio, ToggleLeft, ToggleRight } from 'lucide-react';
import { useStore } from '../store/useStore';
import type { Device } from '../store/useStore';
import { cn } from '../lib/utils';

const TYPE_ICON: Record<string, React.ElementType> = {
    sensor: Thermometer,
    actuator: Zap,
    controller: Cpu,
    virtual: Radio,
};

const TYPE_LABEL: Record<string, string> = {
    sensor: 'Сенсор',
    actuator: 'Исполнитель',
    controller: 'Контроллер',
    virtual: 'Виртуальный',
};

function deviceIsOn(device: Device): boolean | null {
    const s = device.state as Record<string, unknown>;
    if (s.on !== undefined) return Boolean(s.on);
    if (s.power !== undefined) return Boolean(s.power);
    if (s.active !== undefined) return Boolean(s.active);
    if (s.state !== undefined) return s.state === 'on' || s.state === true;
    return null; // no clear on/off state
}

function formatLastSeen(ts: number | null): string {
    if (!ts) return 'Никогда';
    const diff = Math.floor(Date.now() / 1000 - ts);
    if (diff < 60) return `${diff}с назад`;
    if (diff < 3600) return `${Math.floor(diff / 60)}м назад`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}ч назад`;
    return `${Math.floor(diff / 86400)}д назад`;
}

function StatePreview({ state }: { state: Record<string, unknown> }) {
    const entries = Object.entries(state).slice(0, 3);
    if (entries.length === 0)
        return <span className="text-zinc-600 italic">нет данных</span>;
    return (
        <span className="font-mono text-zinc-400 text-xs">
            {entries.map(([k, v]) => `${k}:${JSON.stringify(v)}`).join(' · ')}
        </span>
    );
}

export default function Devices() {
    const devices = useStore((s) => s.devices);
    const devicesLoading = useStore((s) => s.devicesLoading);
    const fetchDevices = useStore((s) => s.fetchDevices);
    const updateDeviceState = useStore((s) => s.updateDeviceState);
    const [search, setSearch] = useState('');
    const [typeFilter, setTypeFilter] = useState<string>('all');

    useEffect(() => {
        fetchDevices();
    }, [fetchDevices]);

    const filtered = devices.filter((d) => {
        const matchSearch =
            d.name.toLowerCase().includes(search.toLowerCase()) ||
            d.protocol.toLowerCase().includes(search.toLowerCase());
        const matchType = typeFilter === 'all' || d.type === typeFilter;
        return matchSearch && matchType;
    });

    const types = ['all', ...Array.from(new Set(devices.map((d) => d.type)))];

    return (
        <div className="max-w-6xl mx-auto space-y-8">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-semibold tracking-tight">Устройства</h1>
                    <p className="text-zinc-400 mt-1">
                        Device Registry — {devices.length} устройств зарегистрировано.
                    </p>
                </div>
                <button
                    onClick={() => fetchDevices()}
                    className="p-2 text-zinc-400 hover:text-zinc-50 hover:bg-zinc-800 rounded-lg transition-colors"
                    title="Обновить"
                >
                    <RefreshCw size={18} className={devicesLoading ? 'animate-spin' : ''} />
                </button>
            </div>

            {/* Filters */}
            <div className="flex items-center gap-4 flex-wrap">
                <div className="relative flex-1 max-w-sm">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" size={16} />
                    <input
                        type="text"
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        placeholder="Поиск по имени или протоколу..."
                        className="w-full bg-zinc-900 border border-zinc-800 rounded-lg pl-9 pr-4 py-2 text-sm text-zinc-50 focus:outline-none focus:border-emerald-500 transition-colors"
                    />
                </div>
                <div className="flex gap-2">
                    {types.map((t) => (
                        <button
                            key={t}
                            onClick={() => setTypeFilter(t)}
                            className={cn(
                                'px-3 py-1.5 rounded-lg text-xs font-medium transition-colors',
                                typeFilter === t
                                    ? 'bg-zinc-700 text-zinc-50'
                                    : 'bg-zinc-900 border border-zinc-800 text-zinc-400 hover:text-zinc-50'
                            )}
                        >
                            {t === 'all' ? 'Все' : (TYPE_LABEL[t] ?? t)}
                        </button>
                    ))}
                </div>
            </div>

            {/* Device list */}
            <div className="bg-zinc-900/50 border border-zinc-800 rounded-2xl overflow-hidden">
                {devicesLoading && filtered.length === 0 && (
                    <div className="p-10 text-center text-zinc-500 text-sm">Загрузка...</div>
                )}
                {!devicesLoading && filtered.length === 0 && (
                    <div className="p-10 text-center text-zinc-500 text-sm">
                        {devices.length === 0
                            ? 'Нет зарегистрированных устройств. Добавьте устройства через Core API.'
                            : 'Ничего не найдено по фильтру.'}
                    </div>
                )}

                <div className="divide-y divide-zinc-800">
                    {filtered.map((device) => {
                        const Icon = TYPE_ICON[device.type] ?? Radio;
                        const on = deviceIsOn(device);
                        return (
                            <div
                                key={device.device_id}
                                className="p-4 flex items-center justify-between hover:bg-zinc-800/20 transition-colors"
                            >
                                <div className="flex items-center gap-4 min-w-0">
                                    <div className="w-10 h-10 rounded-xl bg-zinc-800 flex items-center justify-center text-zinc-400 shrink-0">
                                        <Icon size={18} />
                                    </div>
                                    <div className="min-w-0">
                                        <div className="font-medium text-sm flex items-center gap-2">
                                            {device.name}
                                            <span className="text-xs px-2 py-0.5 rounded-md bg-zinc-800 text-zinc-400 border border-zinc-700">
                                                {device.protocol}
                                            </span>
                                            <span className="text-xs text-zinc-600">
                                                {TYPE_LABEL[device.type] ?? device.type}
                                            </span>
                                        </div>
                                        <div className="text-xs text-zinc-500 mt-1 truncate">
                                            <StatePreview state={device.state as Record<string, unknown>} />
                                        </div>
                                    </div>
                                </div>

                                <div className="flex items-center gap-4 shrink-0 ml-4">
                                    <span className="text-xs text-zinc-600 hidden md:block whitespace-nowrap">
                                        {formatLastSeen(device.last_seen)}
                                    </span>

                                    {on !== null ? (
                                        <button
                                            onClick={() =>
                                                updateDeviceState(device.device_id, { on: !on })
                                            }
                                            className={cn(
                                                'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors',
                                                on
                                                    ? 'bg-emerald-500/15 text-emerald-400 hover:bg-emerald-500/25'
                                                    : 'bg-zinc-800 text-zinc-400 hover:bg-zinc-700'
                                            )}
                                        >
                                            {on ? <ToggleRight size={14} /> : <ToggleLeft size={14} />}
                                            {on ? 'Вкл' : 'Выкл'}
                                        </button>
                                    ) : (
                                        <span className="text-xs text-zinc-600 px-3 py-1.5">—</span>
                                    )}
                                </div>
                            </div>
                        );
                    })}
                </div>
            </div>
        </div>
    );
}
