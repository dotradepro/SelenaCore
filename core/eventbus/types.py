"""
core/eventbus/types.py — Event Bus event type constants
"""

# Core events (published only by the core — core.*)
CORE_INTEGRITY_VIOLATION = "core.integrity_violation"
CORE_INTEGRITY_RESTORED = "core.integrity_restored"
CORE_SAFE_MODE_ENTERED = "core.safe_mode_entered"
CORE_SAFE_MODE_EXITED = "core.safe_mode_exited"
CORE_STARTUP = "core.startup"
CORE_SHUTDOWN = "core.shutdown"

CORE_EVENTS = {
    CORE_INTEGRITY_VIOLATION,
    CORE_INTEGRITY_RESTORED,
    CORE_SAFE_MODE_ENTERED,
    CORE_SAFE_MODE_EXITED,
    CORE_STARTUP,
    CORE_SHUTDOWN,
}

# Device events
DEVICE_STATE_CHANGED = "device.state_changed"
DEVICE_REGISTERED = "device.registered"
DEVICE_REMOVED = "device.removed"
DEVICE_OFFLINE = "device.offline"
DEVICE_ONLINE = "device.online"
DEVICE_DISCOVERED = "device.discovered"
DEVICE_POWER_READING = "device.power_reading"
# payload: {"device_id": str, "watts": float,
#           "volts": float | None, "amps": float | None,
#           "ts": float, "source": str}
# Published by any module that owns a metered device (e.g. device-control
# for Tuya plugs). Energy Monitor subscribes to this — never reaches into
# device.state_changed for power data.

# Module events
MODULE_INSTALLED = "module.installed"
MODULE_STOPPED = "module.stopped"
MODULE_STARTED = "module.started"
MODULE_ERROR = "module.error"
MODULE_REMOVED = "module.removed"

# Sync events
SYNC_COMMAND_RECEIVED = "sync.command_received"
SYNC_COMMAND_ACK = "sync.command_ack"
SYNC_CONNECTION_LOST = "sync.connection_lost"
SYNC_CONNECTION_RESTORED = "sync.connection_restored"

# Voice events
VOICE_WAKE_WORD = "voice.wake_word"
VOICE_RECOGNIZED = "voice.recognized"
VOICE_INTENT = "voice.intent"
VOICE_RESPONSE = "voice.response"
VOICE_PRIVACY_ON = "voice.privacy_on"
VOICE_PRIVACY_OFF = "voice.privacy_off"
VOICE_SPEAK = "voice.speak"
VOICE_SPEAK_DONE = "voice.speak_done"
VOICE_TTS_START = "voice.tts_start"
VOICE_TTS_DONE = "voice.tts_done"

# Notification events
NOTIFICATION_SENT = "notification.sent"

# Registry events
REGISTRY_ENTITY_CHANGED = "registry.entity_changed"
# payload: {"entity_type": "radio_station"|"device"|"scene"|"module",
#           "entity_id": 42, "action": "created"|"updated"|"deleted"}
