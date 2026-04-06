#!/usr/bin/env python3
"""
sdk/cli.py — smarthome CLI: new-module / dev / test / publish

Usage:
  smarthome new-module <name>    — scaffold a new module project
  smarthome dev                  — start module in dev mode with hot reload
  smarthome test                 — run module tests
  smarthome publish <path>       — package and upload module to SelenaCore
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import textwrap
import zipfile
from pathlib import Path


# ---- Scaffolding ----

MODULE_MANIFEST_TEMPLATE = """\
{{
  "name": "{name}",
  "version": "0.1.0",
  "description": "A SelenaCore module",
  "type": "UI",
  "api_version": "1.0",
  "runtime_mode": "always_on",
  "permissions": ["devices.read", "events.subscribe", "events.publish"],
  "intents": [],
  "publishes": []
}}
"""

MODULE_MAIN_TEMPLATE = '''\
"""
{name} — SelenaCore module (WebSocket bus client)
"""
import asyncio
from sdk.base_module import SmartHomeModule, intent, on_event, scheduled


class Module(SmartHomeModule):
    name = "{name}"
    version = "0.1.0"

    async def on_start(self) -> None:
        self._log.info("Module %s started", self.name)

    async def on_stop(self) -> None:
        self._log.info("Module %s stopped", self.name)


if __name__ == "__main__":
    module = Module()
    asyncio.run(module.start())
'''

DOCKERFILE_TEMPLATE = """\
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "main.py"]
"""


def cmd_new_module(name: str) -> None:
    """Scaffold a new module directory."""
    if not name.replace("-", "").replace("_", "").isalnum():
        print(f"Invalid module name: {name!r}. Use only letters, numbers, - and _")
        sys.exit(1)

    target = Path(name)
    if target.exists():
        print(f"Directory {name}/ already exists")
        sys.exit(1)

    target.mkdir()
    (target / "manifest.json").write_text(MODULE_MANIFEST_TEMPLATE.format(name=name))
    (target / "main.py").write_text(MODULE_MAIN_TEMPLATE.format(name=name))
    (target / "requirements.txt").write_text("websockets\nhttpx\n")
    (target / "Dockerfile").write_text(DOCKERFILE_TEMPLATE)
    (target / "tests").mkdir()
    (target / "tests" / "__init__.py").touch()

    print(f"✓ Module scaffolded in {name}/")
    print(f"  Edit {name}/main.py and {name}/manifest.json to get started")


def cmd_dev() -> None:
    """Start module in dev mode (connects to core bus)."""
    os.execvp(sys.executable, [sys.executable, "main.py"])


def cmd_test() -> None:
    """Run module tests with pytest."""
    os.execvp("pytest", ["pytest", "tests/", "-v"])


def cmd_publish(path: str, core_url: str, token: str) -> None:
    """Package module as ZIP and upload to SelenaCore."""
    module_dir = Path(path)
    manifest_file = module_dir / "manifest.json"
    if not manifest_file.exists():
        print(f"manifest.json not found in {path}")
        sys.exit(1)

    manifest = json.loads(manifest_file.read_text())
    name = manifest["name"]
    version = manifest["version"]
    zip_name = f"{name}-{version}.zip"

    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file in module_dir.rglob("*"):
            if file.is_file() and ".git" not in str(file) and "__pycache__" not in str(file):
                zf.write(file, arcname=file.relative_to(module_dir))
    zip_bytes = buf.getvalue()

    print(f"Uploading {name} v{version} ({len(zip_bytes)} bytes)...")
    import urllib.request
    req = urllib.request.Request(
        f"{core_url}/api/v1/modules/install",
        data=zip_bytes,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/zip",
            "X-Module-Name": name,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(f"✓ Uploaded: HTTP {resp.status}")
    except Exception as exc:
        print(f"✗ Upload failed: {exc}")
        sys.exit(1)


# ---- Main ----

def main() -> None:
    parser = argparse.ArgumentParser(
        description="SelenaCore Module SDK CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    p_new = sub.add_parser("new-module", help="Scaffold a new module")
    p_new.add_argument("name", help="Module name")

    sub.add_parser("dev", help="Start module in dev mode (connects to bus)")

    sub.add_parser("test", help="Run module tests")

    p_pub = sub.add_parser("publish", help="Publish module to SelenaCore")
    p_pub.add_argument("path", nargs="?", default=".", help="Module directory")
    p_pub.add_argument("--core", default=os.environ.get("SELENA_CORE_API", "http://localhost"), help="Core API URL")
    p_pub.add_argument("--token", default=os.environ.get("MODULE_TOKEN", ""), help="Auth token")

    args = parser.parse_args()

    if args.command == "new-module":
        cmd_new_module(args.name)
    elif args.command == "dev":
        cmd_dev()
    elif args.command == "test":
        cmd_test()
    elif args.command == "publish":
        cmd_publish(args.path, args.core, args.token)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
