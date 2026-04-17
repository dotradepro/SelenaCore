"""
tests/test_ollama_config_migration.py — one-shot migration
llm.ollama_url → voice.providers.ollama.url.

Covers the five edge cases the Plan agent flagged:
  1. old_only    — legacy key present, new missing  → rewrite + drop old.
  2. new_only    — canonical key present            → no-op.
  3. both        — legacy and canonical coexist     → drop old, keep new.
  4. corrupt     — invalid YAML                     → no-op, no crash.
  5. missing     — no core.yaml on disk             → no-op, no crash.

Atomicity: the migration writes via os.replace, so a failure mid-write
leaves the original file intact. We don't try to simulate that here —
the test focuses on the state transition.
"""
from __future__ import annotations

from pathlib import Path

import yaml


def _write(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data, default_flow_style=False, allow_unicode=True))


def _read(path: Path) -> dict:
    return yaml.safe_load(path.read_text()) or {}


def test_migration_old_only(tmp_path):
    cfg = tmp_path / "core.yaml"
    _write(cfg, {"llm": {"ollama_url": "http://old.invalid:11434", "default_model": "phi3"}})

    from core.config import migrate_ollama_url_key
    changed = migrate_ollama_url_key(cfg)
    assert changed is True

    result = _read(cfg)
    assert result["voice"]["providers"]["ollama"]["url"] == "http://old.invalid:11434"
    # Old key gone.
    assert "ollama_url" not in result.get("llm", {})
    # default_model is preserved inside llm.*.
    assert result["llm"]["default_model"] == "phi3"


def test_migration_new_only(tmp_path):
    cfg = tmp_path / "core.yaml"
    _write(cfg, {"voice": {"providers": {"ollama": {"url": "http://new.invalid"}}}})

    from core.config import migrate_ollama_url_key
    changed = migrate_ollama_url_key(cfg)
    assert changed is False  # nothing to migrate

    result = _read(cfg)
    assert result["voice"]["providers"]["ollama"]["url"] == "http://new.invalid"


def test_migration_both_drops_old_keeps_new(tmp_path):
    cfg = tmp_path / "core.yaml"
    _write(cfg, {
        "llm": {"ollama_url": "http://legacy.invalid"},
        "voice": {"providers": {"ollama": {"url": "http://canonical.invalid"}}},
    })

    from core.config import migrate_ollama_url_key
    changed = migrate_ollama_url_key(cfg)
    assert changed is True  # legacy key dropped

    result = _read(cfg)
    assert result["voice"]["providers"]["ollama"]["url"] == "http://canonical.invalid"
    assert "ollama_url" not in result.get("llm", {})


def test_migration_corrupt_yaml_is_noop(tmp_path):
    cfg = tmp_path / "core.yaml"
    # Intentionally broken YAML — unclosed bracket.
    cfg.write_text("voice: {providers: {ollama: {url:\n")

    from core.config import migrate_ollama_url_key
    changed = migrate_ollama_url_key(cfg)
    assert changed is False

    # File untouched (the fallback is "leave the original alone").
    assert cfg.read_text().startswith("voice:")


def test_migration_missing_file_is_noop(tmp_path):
    cfg = tmp_path / "core.yaml"
    # Never created.

    from core.config import migrate_ollama_url_key
    changed = migrate_ollama_url_key(cfg)
    assert changed is False
    assert not cfg.exists()


def test_migration_removes_empty_llm_section(tmp_path):
    """After migration, if llm section only contained ollama_url, it's pruned."""
    cfg = tmp_path / "core.yaml"
    _write(cfg, {"llm": {"ollama_url": "http://old.invalid"}})

    from core.config import migrate_ollama_url_key
    changed = migrate_ollama_url_key(cfg)
    assert changed is True

    result = _read(cfg)
    assert "llm" not in result  # pruned
    assert result["voice"]["providers"]["ollama"]["url"] == "http://old.invalid"


def test_migration_is_idempotent(tmp_path):
    cfg = tmp_path / "core.yaml"
    _write(cfg, {"llm": {"ollama_url": "http://legacy.invalid"}})

    from core.config import migrate_ollama_url_key

    assert migrate_ollama_url_key(cfg) is True
    # Second run → nothing to do.
    assert migrate_ollama_url_key(cfg) is False

    result = _read(cfg)
    assert result["voice"]["providers"]["ollama"]["url"] == "http://legacy.invalid"
    assert "llm" not in result
