"""system_modules/voice_core/action_phrasing.py — action_context → English text.

Replaces the deprecated LLM rephrase step. SystemModule.speak_action()
publishes ``voice.speak`` events with a structured ``action_context`` dict
like ``{"intent": "device.on", "result": "ok", "device_name": "kitchen
light", "location": "kitchen"}``. Voice Core used to forward that to an
LLM to produce a natural-language reply; now we format it here in plain
English, and OutputTranslator handles the conversion to the user's TTS
language right before Piper synthesis.

The formatter is intentionally verbose rather than generic: a handful of
templated sentences is faster and more reliable than a second LLM round
trip, and the catalog lives in one place rather than being scattered
across modules.
"""
from __future__ import annotations

from typing import Any, Callable


def _with_location(base: str, location: str | None) -> str:
    if not location:
        return base
    # Avoid duplication when the device name already encodes the room
    # (e.g. "kitchen light" in "kitchen" → just "kitchen light").
    if location.lower() in base.lower():
        return base
    return f"{base} in the {location}"


def _article(word: str) -> str:
    """Return "a" or "an" for the given word, best-effort."""
    return "an" if word and word[:1].lower() in "aeiou" else "a"


def _device_label(ctx: dict[str, Any]) -> str:
    """Pick the word the user will hear in the spoken reply.

    Preference order, mirroring what the user literally said:
      1. ``name_en`` — user explicitly referenced the device name
         ("увімкни торшер" → "torshir" in params → speak the name).
      2. ``entity`` — user referenced the device TYPE ("включи світло"
         → ``entity="light"`` → speak "light", NOT the custom Кабінет
         nickname the owner typed when adding the device).
      3. ``device_name`` — last resort, the registry friendly name.
      4. ``"device"`` catch-all.
    """
    name_en = (ctx.get("name_en") or "").strip()
    if name_en:
        return name_en.replace("_", " ")
    entity = (ctx.get("entity") or "").strip()
    if entity:
        return entity.replace("_", " ")
    name = (ctx.get("device_name") or "").strip()
    if name:
        return name
    return "device"


# ── device-control ───────────────────────────────────────────────────────


def _fmt_group_common(ctx: dict[str, Any], verb_present: str, verb_past: str) -> str | None:
    """Shared phrasing for group_ok / group_error / needs_location.

    Returns a sentence or None if the result isn't a group-state result.
    ``verb_present`` is e.g. "Turning on", ``verb_past`` is "Turned on".
    """
    result = ctx.get("result")
    entity = (ctx.get("entity") or "device").replace("_", " ")
    loc = ctx.get("location")
    count = ctx.get("count", 0)
    if result == "needs_location":
        return f"Which room? There's more than one {entity} I can reach."
    if result == "group_ok":
        suffix = "s" if count != 1 and not entity.endswith("s") else ""
        return _with_location(f"{verb_past} {count} {entity}{suffix}", loc) + "."
    if result == "group_error":
        return _with_location(f"I couldn't reach any {entity}", loc) + "."
    return None


def _fmt_device_on(ctx: dict[str, Any]) -> str:
    result = ctx.get("result", "ok")
    group = _fmt_group_common(ctx, "Turning on", "Turned on")
    if group:
        return group
    if result == "not_found":
        loc = ctx.get("location")
        entity = (ctx.get("entity") or "device").replace("_", " ")
        return _with_location(f"I couldn't find {_article(entity)} {entity}", loc) + "."
    if result == "driver_error":
        return f"Sorry, I couldn't reach the {_device_label(ctx)}."
    return _with_location(f"Turning on the {_device_label(ctx)}", ctx.get("location")) + "."


def _fmt_device_off(ctx: dict[str, Any]) -> str:
    result = ctx.get("result", "ok")
    group = _fmt_group_common(ctx, "Turning off", "Turned off")
    if group:
        return group
    if result == "not_found":
        loc = ctx.get("location")
        entity = (ctx.get("entity") or "device").replace("_", " ")
        return _with_location(f"I couldn't find {_article(entity)} {entity}", loc) + "."
    if result == "driver_error":
        return f"Sorry, I couldn't reach the {_device_label(ctx)}."
    return _with_location(f"Turning off the {_device_label(ctx)}", ctx.get("location")) + "."


def _fmt_device_lock(ctx: dict[str, Any]) -> str:
    group = _fmt_group_common(ctx, "Locking", "Locked")
    if group:
        return group
    if ctx.get("result") == "not_found":
        return "I couldn't find that lock."
    if ctx.get("result") == "driver_error":
        return f"Sorry, I couldn't reach the {_device_label(ctx)}."
    return f"Locking the {_device_label(ctx)}."


def _fmt_device_unlock(ctx: dict[str, Any]) -> str:
    group = _fmt_group_common(ctx, "Unlocking", "Unlocked")
    if group:
        return group
    if ctx.get("result") == "not_found":
        return "I couldn't find that lock."
    if ctx.get("result") == "driver_error":
        return f"Sorry, I couldn't reach the {_device_label(ctx)}."
    return f"Unlocking the {_device_label(ctx)}."


def _fmt_device_set_temperature(ctx: dict[str, Any]) -> str:
    group = _fmt_group_common(ctx, "Setting", "Set")
    if group:
        return group
    if ctx.get("result") == "not_found":
        return "I couldn't find a thermostat to adjust."
    if ctx.get("result") == "driver_error":
        return f"Sorry, I couldn't adjust the {_device_label(ctx)}."
    temp = ctx.get("temperature")
    if temp is None:
        return f"Updating the {_device_label(ctx)}."
    return f"Setting the {_device_label(ctx)} to {temp} degrees."


def _fmt_device_set_mode(ctx: dict[str, Any]) -> str:
    group = _fmt_group_common(ctx, "Switching", "Switched")
    if group:
        return group
    mode = ctx.get("mode") or "the selected mode"
    return f"Setting the {_device_label(ctx)} to {mode} mode."


def _fmt_device_set_fan_speed(ctx: dict[str, Any]) -> str:
    group = _fmt_group_common(ctx, "Setting fan speed", "Set fan speed")
    if group:
        return group
    speed = ctx.get("fan_speed") or "medium"
    return f"Setting the fan speed to {speed}."


def _fmt_query_temperature(ctx: dict[str, Any]) -> str:
    result = ctx.get("result", "ok")
    loc = ctx.get("location")
    if result == "not_found":
        return _with_location("I couldn't find a thermometer", loc) + "."
    if result == "no_data":
        return f"I don't have a reading from the {_device_label(ctx)} right now."
    temp = ctx.get("temperature")
    if temp is None:
        return "I couldn't read the temperature."
    if loc:
        return f"It's {temp} degrees in the {loc}."
    return f"It's {temp} degrees on the {_device_label(ctx)}."


# ── clock ────────────────────────────────────────────────────────────────


def _fmt_clock(ctx: dict[str, Any]) -> str:
    action = ctx.get("action", "")
    if action == "alarm_created":
        time = ctx.get("time") or "the requested time"
        return f"Alarm set for {time}."
    if action == "alarm_failed":
        return "I couldn't set that alarm."
    if action == "timer_started":
        secs = int(ctx.get("duration_sec") or 0)
        if secs >= 3600 and secs % 3600 == 0:
            hours = secs // 3600
            return f"Timer started for {hours} hour{'s' if hours != 1 else ''}."
        if secs >= 60:
            mins = secs // 60
            return f"Timer started for {mins} minute{'s' if mins != 1 else ''}."
        return f"Timer started for {secs} seconds."
    if action == "timer_failed":
        return "I couldn't start the timer."
    if action == "reminder_created":
        label = (ctx.get("label") or "").strip()
        secs = int(ctx.get("in_seconds") or 0)
        if secs >= 60:
            mins = secs // 60
            return f"I'll remind you to {label} in {mins} minute{'s' if mins != 1 else ''}." if label else f"Reminder set for {mins} minutes."
        return f"I'll remind you to {label} in {secs} seconds." if label else f"Reminder set for {secs} seconds."
    if action == "reminder_failed":
        return "I couldn't create that reminder."
    if action == "alarms_listed":
        count = int(ctx.get("count") or 0)
        if count == 0:
            return "You have no active alarms."
        if count == 1:
            alarms = ctx.get("alarms") or []
            first = alarms[0]["time"] if alarms else "an alarm"
            return f"You have one alarm set for {first}."
        return f"You have {count} active alarms."
    if action == "alarm_cancelled":
        time = ctx.get("time")
        return f"Cancelled the alarm for {time}." if time else "Alarm cancelled."
    if action == "alarm_cancel_failed":
        return "There are no alarms to cancel."
    if action == "alarm_dismissed":
        count = int(ctx.get("count") or 0)
        return "Alarm stopped." if count else "No alarm is ringing."
    if action == "timer_cancelled":
        count = int(ctx.get("count") or 0)
        if count == 0:
            return "There are no timers to cancel."
        if count == 1:
            return "Timer cancelled."
        return f"Cancelled {count} timers."
    return "Done."


# ── media-player ─────────────────────────────────────────────────────────


def _fmt_media(ctx: dict[str, Any]) -> str:
    action = ctx.get("action", "")
    if action == "already_playing_radio":
        return f"Already playing {ctx.get('station', 'that station')}."
    if action == "no_stations":
        return "You don't have any radio stations configured."
    if action == "genre_not_found":
        genre = ctx.get("genre") or "that genre"
        return f"I couldn't find a station for {genre}."
    if action == "play_station":
        station = ctx.get("station") or "the station"
        return f"Playing {station}."
    if action == "station_not_found":
        name = ctx.get("name") or "that station"
        return f"I couldn't find {name}."
    if action == "play_track":
        label = ctx.get("label") or "the track"
        return f"Playing {label}."
    if action == "usb_not_found":
        return "I couldn't find that track on the USB drive."
    if action == "next":
        return "Skipping to the next track."
    if action == "previous":
        return "Going back to the previous track."
    if action == "stop":
        return "Playback stopped."
    if action == "paused":
        return "Paused."
    if action == "resumed":
        return "Resuming playback."
    if action == "volume_level":
        level = ctx.get("level")
        return f"The volume is {level}." if level is not None else "Here is the current volume."
    if action == "volume_set":
        level = ctx.get("level")
        return f"Volume set to {level}." if level is not None else "Volume updated."
    if action == "nothing_playing":
        return "Nothing is playing right now."
    if action == "shuffle_on":
        return "Shuffle is on."
    if action == "shuffle_off":
        return "Shuffle is off."
    return "Done."


# ── weather-service ──────────────────────────────────────────────────────


def _fmt_weather(ctx: dict[str, Any]) -> str:
    action = ctx.get("action", "")
    if action == "not_ready":
        return "Weather data isn't available yet."
    if action == "no_data":
        return "I don't have current weather data right now."
    if action == "forecast_multi":
        days = ctx.get("days") or []
        if not days:
            return "I don't have a forecast right now."
        labels = ("tomorrow", "day after tomorrow", "in 3 days")
        parts: list[str] = []
        for i, d in enumerate(days[:3]):
            label = labels[i] if i < len(labels) else f"day {i + 1}"
            hi = d.get("temp_max")
            lo = d.get("temp_min")
            cond = d.get("condition")
            if hi is not None and lo is not None:
                piece = f"{label}: {hi} to {lo} degrees"
            elif hi is not None:
                piece = f"{label}: {hi} degrees"
            else:
                piece = label
            if cond:
                piece += f", {cond}"
            parts.append(piece)
        return "Forecast — " + "; ".join(parts) + "."
    temp = ctx.get("temperature")
    cond = ctx.get("condition") or ctx.get("summary")
    if temp is not None and cond:
        return f"It's {temp} degrees and {cond}."
    if temp is not None:
        return f"It's {temp} degrees outside."
    if cond:
        return f"It's {cond} outside."
    return "Here's the current weather."


# ── automation-engine ────────────────────────────────────────────────────


def _fmt_automation(ctx: dict[str, Any]) -> str:
    action = ctx.get("action", "")
    if action == "not_running":
        return "The automation engine isn't running."
    if action == "no_rules":
        return "You don't have any automation rules yet."
    if action == "list":
        count = int(ctx.get("count") or 0)
        names = ctx.get("names") or []
        if count == 0:
            return "No automation rules configured."
        if count == 1 and names:
            return f"You have one automation: {names[0]}."
        return f"You have {count} automations."
    if action == "not_found":
        return f"I couldn't find the automation called {ctx.get('name', 'that')}."
    if action == "enabled":
        return f"Enabled the automation {ctx.get('name', '')}."
    if action == "disabled":
        return f"Disabled the automation {ctx.get('name', '')}."
    if action == "status":
        total = ctx.get("total", 0)
        enabled = ctx.get("enabled", 0)
        return f"{enabled} of {total} automations are enabled."
    return "Done."


# ── presence-detection ───────────────────────────────────────────────────


def _fmt_presence(ctx: dict[str, Any]) -> str:
    action = ctx.get("action", "")
    if action == "module_unavailable":
        return "Presence detection isn't running."
    if action == "no_users":
        return "No users are registered yet."
    if action == "nobody_home":
        return "Nobody is home right now."
    if action == "who_home":
        names = ctx.get("names") or []
        if len(names) == 1:
            return f"{names[0]} is home."
        if len(names) == 2:
            return f"{names[0]} and {names[1]} are home."
        if names:
            return f"{', '.join(names[:-1])}, and {names[-1]} are home."
        return "Someone is home."
    if action == "specify_name":
        return "Who would you like me to look up?"
    if action == "user_not_found":
        return f"I don't know anyone named {ctx.get('name', 'that')}."
    if action == "status":
        home = ctx.get("home", 0)
        total = ctx.get("total", 0)
        return f"{home} of {total} people are home."
    return "Done."


# ── energy-monitor ───────────────────────────────────────────────────────


def _fmt_energy(ctx: dict[str, Any]) -> str:
    action = ctx.get("action", "")
    if action == "not_running":
        return "Energy monitoring isn't running."
    if action == "current":
        watts = ctx.get("watts")
        return f"Current usage is {watts} watts." if watts is not None else "No energy reading available."
    if action == "today":
        kwh = ctx.get("kwh")
        return f"You've used {kwh} kilowatt hours today." if kwh is not None else "No energy total for today."
    return "Done."


# ── device-watchdog ──────────────────────────────────────────────────────


def _fmt_watchdog(ctx: dict[str, Any]) -> str:
    action = ctx.get("action", "")
    if action == "not_running":
        return "The device watchdog isn't running."
    if action == "status":
        online = ctx.get("online", 0)
        offline = ctx.get("offline", 0)
        total = ctx.get("total", 0)
        if offline == 0:
            return f"All {total} devices are online."
        return f"{online} of {total} devices are online — {offline} offline."
    if action == "scan_done":
        total = ctx.get("total", 0)
        return f"Scan complete. {total} devices checked."
    return "Done."


# ── Registry + dispatcher ────────────────────────────────────────────────
#
# Formatters live in a public registry so any module can plug in
# phrasings for its own intents at runtime. The registry ships with
# built-in formatters for the bundled system modules (device-control,
# clock, media-player, weather, automation, presence, energy,
# device-watchdog), but a brand-new module that introduces a new intent
# does NOT need a core edit — it just calls ``register_formatter`` from
# its ``start()`` hook.
#
# Two-level dispatch:
#   1. Per-intent formatter (exact match on intent name)
#   2. Per-namespace fallback (on the ``"<ns>."`` prefix) — used by
#      modules whose phrasing logic is action-based rather than
#      intent-based (e.g. clock reads ctx["action"] to pick between
#      alarm/timer/reminder variants).
#
# Unknown intents fall through to a generic capitalised-verb reply so
# speech never breaks.

_FORMATTERS_REGISTRY: dict[str, Callable[[dict[str, Any]], str]] = {}
_NAMESPACE_REGISTRY: dict[str, Callable[[dict[str, Any]], str]] = {}


def register_formatter(
    intent: str, fn: Callable[[dict[str, Any]], str],
) -> None:
    """Register a per-intent phraser.

    ``fn(ctx) -> str`` receives the ``action_context`` dict published on
    ``voice.speak`` and must return an English sentence. The spoken
    reply is translated to the TTS language by OutputTranslator, so
    templates stay English.
    """
    _FORMATTERS_REGISTRY[intent] = fn


def register_namespace_fallback(
    namespace: str, fn: Callable[[dict[str, Any]], str],
) -> None:
    """Register a namespace-level phraser used when no exact intent
    formatter is registered. ``namespace`` is the prefix before the
    first dot (``"clock"`` for ``"clock.set_alarm"``).
    """
    _NAMESPACE_REGISTRY[namespace] = fn


def unregister_formatter(intent: str) -> None:
    _FORMATTERS_REGISTRY.pop(intent, None)


def _register_builtins() -> None:
    """Wire the bundled formatters into the registry at import time.

    Modules that want to replace a built-in can call
    ``register_formatter`` later — the last registration wins.
    """
    register_formatter("device.on", _fmt_device_on)
    register_formatter("device.off", _fmt_device_off)
    register_formatter("device.lock", _fmt_device_lock)
    register_formatter("device.unlock", _fmt_device_unlock)
    register_formatter("device.set_temperature", _fmt_device_set_temperature)
    register_formatter("device.set_mode", _fmt_device_set_mode)
    register_formatter("device.set_fan_speed", _fmt_device_set_fan_speed)
    register_formatter("device.query_temperature", _fmt_query_temperature)

    register_namespace_fallback("clock", _fmt_clock)
    register_namespace_fallback("media", _fmt_media)
    register_namespace_fallback("weather", _fmt_weather)
    register_namespace_fallback("automation", _fmt_automation)
    register_namespace_fallback("presence", _fmt_presence)
    register_namespace_fallback("energy", _fmt_energy)
    register_namespace_fallback("watchdog", _fmt_watchdog)
    register_namespace_fallback("device_watchdog", _fmt_watchdog)


_register_builtins()


def _get_assistant_name() -> str:
    """Return the English wake-word name for use in chat replies."""
    try:
        from core.config_writer import read_config
        vc = read_config().get("voice", {}) or {}
        name = (vc.get("wake_word_en") or "").strip()
        if name:
            return name.split()[-1].capitalize()
        wake = (vc.get("wake_word_model") or "").strip()
        if wake:
            parts = wake.replace("_", " ").strip().split()
            if parts:
                native = parts[-1]
                if any("\u0400" <= ch <= "\u04ff" for ch in native):
                    from core.translit import cyrillic_to_latin
                    return cyrillic_to_latin(native).capitalize() or "Selena"
                return native.capitalize()
    except Exception:
        pass
    return "Selena"


def _fmt_unknown(ctx: dict[str, Any]) -> str:  # noqa: ARG001
    """Spoken fallback when no intent matched or the LLM parse failed."""
    return "I did not understand that command."


def _fmt_chat(ctx: dict[str, Any]) -> str:  # noqa: ARG001
    """Spoken identity answer for chat/freeform lanes."""
    return f"I am {_get_assistant_name()}, your home assistant."


def _fmt_generic(ctx: dict[str, Any]) -> str:
    """Best-effort generic fallback for intents without a dedicated formatter."""
    action = ctx.get("action") or ctx.get("result") or "done"
    action = str(action).replace("_", " ")
    return f"{action.capitalize()}."


def format_action_context(intent: str, ctx: dict[str, Any] | None = None) -> str:
    """Turn an ``action_context`` dict into an English sentence for TTS.

    Dispatch order:
      1. ``unknown`` / empty intent → fixed fallback sentence
      2. ``chat`` → identity sentence (uses wake-word name)
      3. Exact intent hit in ``_FORMATTERS_REGISTRY``
      4. Namespace hit in ``_NAMESPACE_REGISTRY`` (prefix before first dot)
      5. Generic capitalised-verb fallback — never empty

    Never returns an empty string. Any formatter that raises is caught
    silently and the dispatcher falls through to the next layer so a
    buggy custom formatter cannot break the voice pipeline.
    """
    ctx = ctx or {}

    # Classifier-only lanes: no device state needed.
    if intent == "unknown" or not intent:
        return _fmt_unknown(ctx)
    if intent == "chat":
        return _fmt_chat(ctx)

    if not ctx:
        return "Done."

    # Exact intent formatter
    specific = _FORMATTERS_REGISTRY.get(intent)
    if specific is not None:
        try:
            out = specific(ctx)
            if out:
                return out
        except Exception as exc:
            logger = __import__("logging").getLogger(__name__)
            logger.debug("formatter for %s crashed: %s", intent, exc)

    # Namespace fallback
    ns = intent.split(".", 1)[0] if "." in intent else intent
    ns_fn = _NAMESPACE_REGISTRY.get(ns)
    if ns_fn is not None:
        try:
            out = ns_fn(ctx)
            if out:
                return out
        except Exception as exc:
            logger = __import__("logging").getLogger(__name__)
            logger.debug("namespace formatter for %s crashed: %s", ns, exc)

    return _fmt_generic(ctx)
