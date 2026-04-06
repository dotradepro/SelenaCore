"""
system_modules/ui_core/tty_status.py — TTY1 Textual TUI status display

Shows real-time system stats and device status in the terminal
when no graphical display is available (display_mode == "tty").

On first boot (wizard not completed), shows a QR code and setup instructions.

Run standalone:  python -m system_modules.ui_core.tty_status
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

CORE_API = os.environ.get("CORE_API_URL", "http://localhost/api/v1")
UI_URL_BASE = os.environ.get("UI_URL", "http://localhost")
REFRESH_SEC = 5
WIZARD_STATE_FILE = Path("/var/lib/selena/wizard_state.json")


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


def _get_local_ip() -> str:
    """Return the host's real IP address.
    When running inside Docker, reads HOST_IP env var set by the systemd service.
    Falls back to socket detection (returns container IP inside Docker).
    """
    host_ip = os.environ.get("HOST_IP", "").strip().split()[0] if os.environ.get("HOST_IP", "").strip() else ""
    if host_ip and host_ip != "127.0.0.1":
        return host_ip
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def _is_wizard_done() -> bool:
    """Return True if the onboarding wizard has been completed."""
    try:
        import json
        data = json.loads(WIZARD_STATE_FILE.read_text())
        return bool(data.get("completed"))
    except Exception:
        return False


def _qr_ascii(url: str) -> list[str]:
    """Generate ASCII QR code lines using the qrcode library if available."""
    try:
        import qrcode
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=1,
            border=1,
        )
        qr.add_data(url)
        qr.make(fit=True)
        matrix = qr.get_matrix()
        lines: list[str] = []
        for row in matrix:
            line = ""
            for cell in row:
                line += "██" if cell else "  "
            lines.append(line)
        return lines
    except ImportError:
        url_str = f"  {url}  "
        border = "+" + "-" * len(url_str) + "+"
        return [border, f"|{url_str}|", border]


def _fetch_requirements() -> dict[str, Any]:
    """Fetch wizard requirements synchronously (for TTY render)."""
    import json as _json
    try:
        import urllib.request
        req = urllib.request.Request(
            "http://localhost/api/ui/wizard/requirements",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            return _json.loads(resp.read())
    except Exception:
        return {}


# ── ANSI helpers ──────────────────────────────────────────────────

_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_WHITE = "\033[97m"
_GRAY = "\033[90m"
_GREEN = "\033[32m"
_RED = "\033[31m"
_BLUE = "\033[34m"
_CYAN = "\033[36m"
_BG_DARK = "\033[48;5;234m"  # dark gray bg
_BG_DARKER = "\033[48;5;232m"  # near-black bg


def _pad(text: str, width: int) -> str:
    """Pad text to width accounting for ANSI escape codes."""
    visible = len(text.encode("utf-8").decode("utf-8"))
    # strip ansi for length calc
    import re
    clean = re.sub(r"\033\[[0-9;]*m", "", text)
    pad_needed = width - len(clean)
    return text + " " * max(0, pad_needed)


async def render_setup_screen() -> None:
    """
    First-run setup screen — split-panel layout matching browser SetupLanding.

    Left panel:  QR code + "Scan to set up"
    Right panel: Status checklist + URL + instructions
    """
    hostname = os.uname().nodename
    local_ip = _get_local_ip()
    ui_url = UI_URL_BASE.replace("localhost", local_ip).replace("127.0.0.1", local_ip)

    LEFT_W = 62   # left panel inner width
    RIGHT_W = 62  # right panel inner width

    while True:
        if _is_wizard_done():
            return

        qr_lines = _qr_ascii(ui_url)
        requirements = _fetch_requirements()
        steps = requirements.get("steps", {})

        # ── Build left panel lines ──
        left: list[str] = []
        left.append("")
        left.append(f"{_DIM}MOBILE SETUP{_RESET}")
        left.append("")

        # Center QR block
        for ql in qr_lines:
            left.append(ql)

        left.append("")
        left.append(f"{_BOLD}{_WHITE}Scan to set up{_RESET}")
        left.append(f"{_DIM}{ui_url}{_RESET}")
        left.append("")

        # ── Build right panel lines ──
        right: list[str] = []
        right.append("")
        right.append(f"{_DIM}SELENACORE{_RESET}")
        right.append("")
        for line in "Continue setup\non device".split("\n"):
            right.append(f"{_BOLD}{_WHITE}{line}{_RESET}")
        right.append("")
        right.append(f"{_DIM}Open browser or scan QR{_RESET}")
        right.append("")
        right.append(f"{_DIM}── SETUP STATUS ──{_RESET}")
        right.append("")

        # Checklist
        step_icons = {
            "internet": "◉ Network",
            "admin_user": "◉ Administrator",
            "device_name": "◉ Device name",
            "platform": "◉ Platform",
        }
        for sid, info in steps.items():
            label = step_icons.get(sid, f"◉ {info.get('label', sid)}")
            done = info.get("done", False)
            required = info.get("required", False)
            if done:
                icon = f"{_GREEN}✔{_RESET}"
                text = f"{_WHITE}{label}{_RESET}"
            elif required:
                icon = f"{_RED}✘{_RESET}"
                text = f"{_RED}{label}{_RESET}  {_DIM}{_RED}← required{_RESET}"
            else:
                icon = f"{_DIM}○{_RESET}"
                text = f"{_DIM}{label}{_RESET}"
            right.append(f"  {icon} {text}")

        right.append("")
        right.append(f"{_DIM}─────────────────────────────────{_RESET}")
        right.append("")
        right.append(f"  {_CYAN}▸{_RESET} {_WHITE}{ui_url}{_RESET}")
        right.append(f"  {_DIM}IP: {local_ip}{_RESET}")
        right.append("")
        for line in "Screen will refresh automatically\nafter setup is complete.".split("\n"):
            right.append(f"{_DIM}{line}{_RESET}")
        right.append("")

        # ── Equalize heights ──
        max_h = max(len(left), len(right))
        while len(left) < max_h:
            left.append("")
        while len(right) < max_h:
            right.append("")

        # ── Render ──
        _clear()

        # Top bar
        total_w = LEFT_W + RIGHT_W + 3  # 3 = "│" separators
        top = f"{'─' * total_w}"
        print(f"{_DIM}{top}{_RESET}")
        title = f"  SelenaCore  •  {hostname}  •  First run"
        print(f"{_BOLD}{_WHITE}{_pad(title, total_w)}{_RESET}")
        print(f"{_DIM}{top}{_RESET}")

        # Side-by-side panels
        sep = f"{_DIM}│{_RESET}"
        for i in range(max_h):
            l_line = _pad(f"  {left[i]}", LEFT_W)
            r_line = _pad(f"  {right[i]}", RIGHT_W)
            print(f"{sep}{l_line}{sep}{r_line}{sep}")

        # Bottom bar
        print(f"{_DIM}{top}{_RESET}")
        ts = time.strftime("%H:%M:%S")
        print(f"{_DIM}  Updated: {ts}{_RESET}")

        await asyncio.sleep(REFRESH_SEC)


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
        # If wizard not done → show setup screen; when it completes, fall through to stats
        if not _is_wizard_done():
            await render_setup_screen()
        await render_loop()
    except (KeyboardInterrupt, asyncio.CancelledError):
        _clear()
        print("SelenaCore TUI stopped.")


if __name__ == "__main__":
    asyncio.run(main())
