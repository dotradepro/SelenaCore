import React from 'react';
import { Link, useLocation } from 'react-router-dom';
import { Home, Settings, Box, Activity, Mic, ShieldAlert, Server } from 'lucide-react';
import { cn } from '../lib/utils';
import { useStore } from '../store/useStore';

export default function Layout({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const user = useStore((state) => state.user);

  const navItems = [
    { icon: Home, label: 'Дашборд', path: '/' },
    { icon: Server, label: 'Устройства', path: '/devices' },
    { icon: Box, label: 'Модули', path: '/modules' },
    { icon: Settings, label: 'Настройки', path: '/settings' },
  ];

  return (
    <div className="flex h-screen bg-zinc-950 text-zinc-50 overflow-hidden font-sans">
      {/* Sidebar */}
      <aside className="w-64 border-r border-zinc-800 bg-zinc-900/50 flex flex-col">
        <div className="p-6 flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-emerald-500/20 text-emerald-500 flex items-center justify-center">
            <Activity size={20} />
          </div>
          <div>
            <h1 className="font-semibold tracking-tight">SmartHome LK</h1>
            <p className="text-xs text-zinc-500">Core v0.3-beta</p>
          </div>
        </div>

        <nav className="flex-1 px-4 space-y-1">
          {navItems.map((item) => {
            const isActive = item.path === '/'
              ? location.pathname === '/'
              : location.pathname === item.path || location.pathname.startsWith(item.path + '/');
            return (
              <Link
                key={item.path}
                to={item.path}
                className={cn(
                  "flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors",
                  isActive 
                    ? "bg-zinc-800 text-zinc-50" 
                    : "text-zinc-400 hover:text-zinc-50 hover:bg-zinc-800/50"
                )}
              >
                <item.icon size={18} />
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="p-4 border-t border-zinc-800">
          <div className="flex items-center gap-3 px-3 py-2">
            <div className="w-8 h-8 rounded-full bg-zinc-800 flex items-center justify-center text-sm font-medium">
              {user?.name?.[0]?.toUpperCase() || 'A'}
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium truncate">{user?.name || 'Admin'}</p>
              <p className="text-xs text-zinc-500 truncate">{user?.role || 'admin'}</p>
            </div>
          </div>
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {/* Top Header */}
        <header className="h-16 border-b border-zinc-800 bg-zinc-950/50 backdrop-blur-md flex items-center justify-between px-8 shrink-0">
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2 text-sm text-zinc-400">
              <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
              Система активна
            </div>
          </div>
          <div className="flex items-center gap-4">
            <button className="p-2 text-zinc-400 hover:text-zinc-50 transition-colors rounded-full hover:bg-zinc-800">
              <ShieldAlert size={20} />
            </button>
            <button className="p-2 text-zinc-400 hover:text-emerald-400 transition-colors rounded-full hover:bg-zinc-800 flex items-center gap-2">
              <Mic size={20} />
              <span className="text-sm font-medium">Слушаю</span>
            </button>
          </div>
        </header>

        {/* Page Content */}
        <div className="flex-1 overflow-auto p-8">
          {children}
        </div>
      </main>
    </div>
  );
}
