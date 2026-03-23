# system_modules/media_player/sources/usb_source.py
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {
    ".mp3", ".flac", ".aac", ".ogg", ".wav",
    ".m4a", ".opus", ".wma", ".ape", ".mpc",
}

MOUNT_DIRS = ["/media", "/mnt", "/run/media"]


class USBSource:
    async def scan(self, mount_base: Optional[str] = None) -> list[dict]:
        """Scan all mounted media and return a list of audio file dicts.

        mount_base: if None, all standard mount directories are scanned.
        """
        tracks: list[dict] = []
        bases = [mount_base] if mount_base else MOUNT_DIRS
        for base_str in bases:
            base = Path(base_str)
            if not base.exists():
                continue
            for filepath in base.rglob("*"):
                if filepath.is_file() and filepath.suffix.lower() in AUDIO_EXTENSIONS:
                    info = await self._file_info(filepath)
                    tracks.append(info)
        return sorted(tracks, key=lambda t: (t["artist"], t["album"], t["title"]))

    async def _file_info(self, path: Path) -> dict:
        info: dict = {
            "path": str(path),
            "name": path.stem,
            "ext": path.suffix.lower(),
            "size_mb": round(path.stat().st_size / 1024 / 1024, 1),
            "title": path.stem,
            "artist": "",
            "album": "",
            "genre": "",
            "year": "",
            "duration_sec": 0,
            "has_cover": False,
        }
        try:
            from mutagen import File as MFile  # type: ignore[import-untyped]

            audio = MFile(str(path), easy=True)
            if audio:
                info["title"] = (audio.get("title") or [path.stem])[0]
                info["artist"] = (audio.get("artist") or [""])[0]
                info["album"] = (audio.get("album") or [""])[0]
                info["genre"] = (audio.get("genre") or [""])[0]
                info["year"] = (audio.get("date") or [""])[0]
                info["duration_sec"] = int(getattr(audio.info, "length", 0))
        except Exception as exc:
            logger.debug("Mutagen parse failed for %s: %s", path, exc)

        # Check for embedded cover art
        try:
            suffix = path.suffix.lower()
            if suffix == ".flac":
                from mutagen.flac import FLAC  # type: ignore[import-untyped]

                info["has_cover"] = bool(FLAC(str(path)).pictures)
            elif suffix == ".mp3":
                from mutagen.id3 import APIC, ID3  # type: ignore[import-untyped]

                tags = ID3(str(path))
                info["has_cover"] = any(isinstance(v, APIC) for v in tags.values())
        except Exception:
            pass

        return info

    def get_mounted_devices(self) -> list[dict]:
        """List mounted USB/SD devices from /proc/mounts."""
        devices: list[dict] = []
        try:
            with open("/proc/mounts") as fh:
                for line in fh:
                    parts = line.split()
                    if len(parts) >= 2:
                        device, mountpoint = parts[0], parts[1]
                        if any(mountpoint.startswith(m) for m in MOUNT_DIRS):
                            devices.append(
                                {"device": device, "mountpoint": mountpoint}
                            )
        except Exception:
            pass
        return devices
