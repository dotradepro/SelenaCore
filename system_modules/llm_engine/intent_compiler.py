"""
system_modules/llm_engine/intent_compiler.py — Compile YAML vocabulary + definitions into regex patterns.

Replaces hand-written intent_patterns.py files with a data-driven approach:
  1. Load vocabulary YAML (verbs, nouns, params, locations) per language
  2. Load definitions YAML (intent templates with {verb.X}, {noun.X}, {param.X})
  3. Expand templates into regex patterns
  4. Output SystemIntentEntry objects for IntentRouter registration

Cache compiled patterns via pickle with hash-based invalidation.
"""
from __future__ import annotations

import hashlib
import logging
import os
import pickle
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from system_modules.llm_engine.intent_router import SystemIntentEntry

logger = logging.getLogger(__name__)


@dataclass
class IntentDefinition:
    """Parsed intent definition from definitions.yaml."""

    name: str
    module: str
    noun_class: str
    verb: str
    priority: int
    description: str
    templates: list[str] = field(default_factory=list)
    params: dict[str, dict[str, str]] = field(default_factory=dict)
    overrides: dict[str, list[str]] = field(default_factory=dict)


class IntentCompiler:
    """Compile vocabulary YAML + definitions YAML into SystemIntentEntry list."""

    def __init__(self, config_dir: str | Path) -> None:
        self._config_dir = Path(config_dir)
        self._vocab_dir = self._config_dir / "vocab"
        self._defs_path = self._config_dir / "definitions.yaml"
        # Use data dir from env or default; works both in container and on host
        data_dir = os.environ.get("CORE_DATA_DIR", "/var/lib/selena")
        self._cache_path = Path(data_dir) / "cache" / "compiled_intents.pkl"

        self._vocabs: dict[str, dict[str, Any]] = {}
        self._definitions: list[IntentDefinition] = []
        self._entries: list[SystemIntentEntry] = []
        self._noun_classes: dict[str, dict[str, Any]] = {}
        self._loaded = False

    # ── Public API ───────────────────────────────────────────────────────

    def load(self, languages: list[str] | None = None) -> None:
        """Load vocabulary + definitions and compile into SystemIntentEntry list."""
        if languages is None:
            languages = ["en", "uk"]

        cache_key = self._compute_cache_key(languages)
        if self._try_load_cache(cache_key):
            self._loaded = True
            logger.info(
                "IntentCompiler: loaded %d intents from cache", len(self._entries),
            )
            return

        self._load_vocabs(languages)
        self._load_definitions()
        self._compile(languages)
        self._save_cache(cache_key)
        self._loaded = True
        logger.info(
            "IntentCompiler: compiled %d intents for %s",
            len(self._entries), languages,
        )

    def match(self, text: str, lang: str = "en") -> dict[str, Any] | None:
        """Match text against compiled patterns. Returns dict or None."""
        if not self._loaded:
            self.load()

        text_lower = text.lower().strip()
        for entry in sorted(self._entries, key=lambda e: e.priority, reverse=True):
            patterns = entry.patterns.get(lang) or entry.patterns.get("en", [])
            for pattern in patterns:
                try:
                    m = re.search(pattern, text_lower, re.IGNORECASE)
                except re.error:
                    continue
                if m:
                    params = {k: v for k, v in m.groupdict().items() if v is not None}
                    defn = self._find_definition(entry.intent)
                    return {
                        "intent": entry.intent,
                        "module": entry.module,
                        "noun_class": defn.noun_class if defn else "UNKNOWN",
                        "verb": defn.verb if defn else "UNKNOWN",
                        "params": params,
                        "source": "system_module",
                    }
        return None

    def get_intents_for_module(self, module_name: str) -> list[SystemIntentEntry]:
        """Return compiled SystemIntentEntry list for a specific module."""
        if not self._loaded:
            self.load()
        return [e for e in self._entries if e.module == module_name]

    def get_all_modules(self) -> list[str]:
        """Return list of all module names that have registered intents."""
        if not self._loaded:
            self.load()
        return list({e.module for e in self._entries})

    def get_all_noun_classes(self) -> list[str]:
        """Return list of all noun_class values from definitions."""
        if not self._loaded:
            self.load()
        return list({d.noun_class for d in self._definitions})

    def get_entities_for_noun_class(self, noun_class: str) -> list[str]:
        """Return entity nouns used in intents of this noun_class (for LLM hints)."""
        if not self._loaded:
            self.load()
        entities: set[str] = set()
        for defn in self._definitions:
            if defn.noun_class != noun_class:
                continue
            for tmpl in defn.templates:
                for m in re.finditer(r"\{noun\.(\w+)\}", tmpl):
                    entities.add(m.group(1))
        return sorted(entities)

    def get_intents_for_noun_class(self, noun_class: str) -> list[str]:
        """Return intent names belonging to a noun_class."""
        if not self._loaded:
            self.load()
        return [d.name for d in self._definitions if d.noun_class == noun_class]

    def get_definition(self, intent_name: str) -> IntentDefinition | None:
        """Return IntentDefinition by intent name."""
        return self._find_definition(intent_name)

    def get_all_definitions(self) -> list[IntentDefinition]:
        """Return all parsed IntentDefinition objects."""
        if not self._loaded:
            self.load()
        return list(self._definitions)

    # ── Compilation ──────────────────────────────────────────────────────

    def _load_vocabs(self, languages: list[str]) -> None:
        self._vocabs = {}
        for lang in languages:
            path = self._vocab_dir / f"{lang}.yaml"
            if path.exists():
                self._vocabs[lang] = yaml.safe_load(path.read_text(encoding="utf-8"))
            else:
                logger.warning("IntentCompiler: vocab file missing: %s", path)

    def _load_definitions(self) -> None:
        if not self._defs_path.exists():
            logger.error("IntentCompiler: definitions file missing: %s", self._defs_path)
            return

        raw = yaml.safe_load(self._defs_path.read_text(encoding="utf-8"))
        self._noun_classes = raw.get("noun_classes", {})
        self._definitions = []

        for intent_name, cfg in raw.get("intents", {}).items():
            self._definitions.append(IntentDefinition(
                name=intent_name,
                module=cfg.get("module", ""),
                noun_class=cfg.get("noun_class", "UNKNOWN"),
                verb=cfg.get("verb", "UNKNOWN"),
                priority=cfg.get("priority", 5),
                description=cfg.get("description", ""),
                templates=cfg.get("templates", []),
                params=cfg.get("params", {}),
                overrides=cfg.get("overrides", {}),
            ))

    def _compile(self, languages: list[str]) -> None:
        self._entries = []

        for defn in self._definitions:
            patterns_by_lang: dict[str, list[str]] = {}

            for lang in languages:
                lang_patterns: list[str] = []

                # 1. Overrides first (exact regex from definitions.yaml)
                overrides = defn.overrides.get(lang, [])
                lang_patterns.extend(overrides)

                # 2. Expand templates using vocabulary
                vocab = self._vocabs.get(lang)
                if vocab and defn.templates:
                    for tmpl in defn.templates:
                        expanded = self._expand_template(tmpl, vocab, defn.params)
                        if expanded:
                            lang_patterns.append(expanded)

                if lang_patterns:
                    patterns_by_lang[lang] = lang_patterns

            if patterns_by_lang:
                self._entries.append(SystemIntentEntry(
                    module=defn.module,
                    intent=defn.name,
                    priority=defn.priority,
                    description=defn.description,
                    patterns=patterns_by_lang,
                ))

    def _expand_template(
        self,
        template: str,
        vocab: dict[str, Any],
        param_defs: dict[str, Any],
    ) -> str | None:
        """Expand a template string into a regex pattern using vocabulary."""
        result = template

        # {verb.play} → (?:play|put on|start|...)
        for m in re.finditer(r"\{verb\.(\w+)\}", template):
            words = self._get_vocab_words(vocab, "verbs", m.group(1))
            if not words:
                return None
            alt = "|".join(re.escape(w).replace(r"\ ", r"\s+") for w in words)
            result = result.replace(m.group(0), f"(?:{alt})")

        # {noun.radio} → (?:radio|music|audio|...)
        for m in re.finditer(r"\{noun\.(\w+)\}", template):
            words = self._get_vocab_words(vocab, "nouns", m.group(1))
            if not words:
                return None
            alt = "|".join(re.escape(w).replace(r"\ ", r"\s+") for w in words)
            result = result.replace(m.group(0), f"(?:{alt})")

        # {param.genre} → (?P<genre>rock|jazz|...)
        # {param.level} → (?P<level>\d+)
        # {param.query} → (?P<query>.+)
        for m in re.finditer(r"\{param\.(\w+)\}", template):
            key = m.group(1)
            pdef = param_defs.get(key, {})
            ptype = pdef.get("type", "enum") if isinstance(pdef, dict) else "enum"
            vocab_key = pdef.get("key", key) if isinstance(pdef, dict) else key

            if ptype == "number":
                group = f"(?P<{key}>\\d+)"
            elif ptype == "freetext":
                group = f"(?P<{key}>.+)"
            else:
                # enum — look up in vocab params
                param_values = vocab.get("params", {}).get(vocab_key)
                if param_values is None:
                    return None
                if isinstance(param_values, str) and param_values == "__NUMBER__":
                    group = f"(?P<{key}>\\d+)"
                elif isinstance(param_values, str) and param_values == "__FREETEXT__":
                    group = f"(?P<{key}>.+)"
                elif isinstance(param_values, list):
                    alt = "|".join(re.escape(v) for v in param_values)
                    group = f"(?P<{key}>{alt})"
                else:
                    return None

            result = result.replace(m.group(0), group)

        # {location} → (?:kitchen|bedroom|...)? (optional)
        if "{location}" in result:
            all_locs = []
            for locs in vocab.get("locations", {}).values():
                if isinstance(locs, list):
                    all_locs.extend(locs)
            if all_locs:
                alt = "|".join(re.escape(loc).replace(r"\ ", r"\s+") for loc in all_locs)
                result = result.replace("{location}", f"(?:{alt})?")
            else:
                result = result.replace("{location}", "")

        # Handle trailing ? (optional preceding word)
        result = re.sub(r"(\([^)]+\))\?", r"\1?", result)

        # Replace spaces with \s+
        result = result.replace(" ", r"\s+")

        # Validate regex
        try:
            re.compile(result)
        except re.error as exc:
            logger.warning("IntentCompiler: invalid regex from template '%s': %s", template, exc)
            return None

        return result

    @staticmethod
    def _get_vocab_words(vocab: dict[str, Any], category: str, key: str) -> list[str]:
        """Extract word list from vocab entry (handles exact, stem, plain list)."""
        entry = vocab.get(category, {}).get(key)
        if entry is None:
            return []
        if isinstance(entry, list):
            return entry
        if isinstance(entry, dict):
            words = list(entry.get("exact", []))
            for stem in entry.get("stem", []):
                words.append(stem + r"\w*")
            words.extend(entry.get("regex", []))
            return words
        return []

    # ── Cache ────────────────────────────────────────────────────────────

    def _compute_cache_key(self, languages: list[str]) -> str:
        files = [self._defs_path] + [self._vocab_dir / f"{l}.yaml" for l in languages]
        parts = []
        for f in files:
            if f.exists():
                parts.append(hashlib.md5(f.read_bytes()).hexdigest())
            else:
                parts.append("missing")
        return ":".join(parts)

    def _try_load_cache(self, expected_key: str) -> bool:
        if not self._cache_path.exists():
            return False
        try:
            data = pickle.loads(self._cache_path.read_bytes())  # noqa: S301
            if data.get("key") != expected_key:
                return False
            self._entries = data["entries"]
            self._definitions = data["definitions"]
            self._noun_classes = data.get("noun_classes", {})
            return True
        except Exception:
            return False

    def _save_cache(self, key: str) -> None:
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_bytes(pickle.dumps({
                "key": key,
                "entries": self._entries,
                "definitions": self._definitions,
                "noun_classes": self._noun_classes,
            }, protocol=4))
        except Exception as exc:
            logger.warning("IntentCompiler: cache save failed: %s", exc)

    # ── Helpers ──────────────────────────────────────────────────────────

    def _find_definition(self, intent_name: str) -> IntentDefinition | None:
        for d in self._definitions:
            if d.name == intent_name:
                return d
        return None


# ── Singleton ────────────────────────────────────────────────────────────

_compiler: IntentCompiler | None = None


def get_intent_compiler() -> IntentCompiler:
    global _compiler
    if _compiler is None:
        _compiler = IntentCompiler("/opt/selena-core/config/intents")
        _compiler.load(["en", "uk"])
    return _compiler
