"""
system_modules/ui_core/tty_status.py — TTY1 Textual TUI status display

Shows real-time system stats and device status in the terminal
when no graphical display is available (display_mode == "tty").

Run standalone:  python -m system_modules.ui_core.tty_status
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

CORE_API = os.environ.get("CORE_API_URL", "http://localhost:7070/api/v1")
REFRESH_SEC = 5


def _core_headers() -> dict[str, str]:
    token_dir = "/secure/module_tokens"
    try:
        from pathlib import Path
        tokens = list(Path(token_dir).glob("*.token"))
        if tokens:
            return {"Authorization": f"Bearer {tokens[0].read_text().strip()}"}
    except Exception:
        pass
    dev = os.environ.get("DEV_MODULE_TOKEN", "")
    return {"Authorization": f"Bearer {dev}"} if dev else {}


async def _fetch(path: str) -> Any:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{CORE_API}{path}", headers=_core_headers())
            resp.raise_for_status()
            return resp.json()
    except Exception:
        return None


def _clear() -> None:
    print("\033[2J\033[H", end="", flush=True)


def _bar(pct: float, width: int = 20) -> str:
    filled = int(width * pct / 100)
    return "█" * filled + "░" * (width - filled)


async def render_loop() -> None:
    """Main TUI render loop — clears screen and redraws every REFRESH_SEC seconds."""
    try:
        import psutil
        has_psutil = True
    except ImportError:
        has_psutil = False

    hostname = os.uname().nodename

    while True:
        health = await _fetch("/health")
        devices_resp = await _fetch("/devices")
        modules_resp = await _fetch("/modules")

        devices = (devices_resp or {}).get("devices", [])
        modules = (modules_resp or {}).get("modules", [])
        status = (health or {}).get("status", "unreachable")
        mode = (health or {}).get("mode", "?")
        integrity = (health or {}).get("integrity", "?")
        uptime = (health or {}).get("uptime", 0)

        _clear()
        print("╔══════════════════════════════════════════════════════╗")
        print(f"║   SelenaCore  •  {hostname:<30}   ║")
        print("╚══════════════════════════════════════════════════════╝")
        print()

        status_icon = "✔" if status == "ok" else "✘"
        print(f"  Core status : {status_icon} {status}  [{mode}]")
        print(f"  Integrity   : {integrity}")
        print(f"  Uptime      : {_fmt_uptime(uptime)}")
        print()

        if has_psutil:
            import psutil
            cpu = psutil.cpu_percent(interval=None)
            ram = psutil.virtual_memory()
            disk = psutil.disk_usage("/")
            print(f"  CPU   [{_bar(cpu)}] {cpu:.0f}%")
            print(f"  RAM   [{_bar(ram.percent)}] {ram.used // (1024**2)}MB / {ram.total // (1024**2)}MB")
            print(f"  Disk  [{_bar(disk.percent)}] {disk.used / 1e9:.1f}GB / {disk.total / 1e9:.1f}GB")
            try:
                temps = psutil.sensors_temperatures()
                if temps:
                    sensor = next(iter(temps.values()))
                    if sensor:
                        print(f"  Temp  {sensor[0].current:.0f}°C")
            except Exception:
                pass
            print()

        print(f"  Devices [{len(devices)}]:")
        for d in devices[:5]:
            state_preview = str(d.get("state", {}))[:30]
            print(f"    • {d['name']:<25} {state_preview}")
        if len(devices) > 5:
            print(f"    … {len(devices) - 5} more")
        print()

        print(f"  Modules [{len(modules)}]:")
        for m in modules[:5]:
            icon = "▶" if m.get("status") == "RUNNING" else "■"
            print(f"    {icon} {m['name']:<25} :{m.get('port', '?')}")
        if len(modules) > 5:
            print(f"    … {len(modules) - 5} more")

        print()
        print(f"  ─── Updated: {time.strftime('%H:%M:%S')} ─── Press Ctrl+C to exit ───")

        await asyncio.sleep(REFRESH_SEC)


def _fmt_uptime(seconds: float) -> str:
    h = int(seconds) // 3600
    m = (int(seconds) % 3600) // 60
    s = int(seconds) % 60
    return f"{h}h {m}m {s}s"


async def main() -> None:
    try:
        await render_loop()
    except (KeyboardInterrupt, asyncio.CancelledError):
        _clear()
        print("SelenaCore TUI stopped.")


if __name__ == "__main__":
    asyncio.run(main())
