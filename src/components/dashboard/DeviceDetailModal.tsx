import { useEffect, useState } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { useTranslation } from 'react-i18next';
import { useStore, type Device } from '../../store/useStore';
import Icon from './templates/Icon';
import { entityIconForFrontend } from './deviceIcons';

/** Bottom-sheet modal that surfaces a device's full parameter set when
 *  the user long-presses (touch) or right-clicks (mouse) a toggle-list
 *  item. Restores the depth of control the V1 widget.html iframes used
 *  to expose — V2 toggle-list only had on/off; everything else was
 *  invisible until the user dug into the module's settings page.
 *
 *  The modal:
 *    - Reads the live ``Device`` from the store (capabilities + state).
 *    - Renders a control per capability via :func:`renderCapabilityControl`.
 *    - Calls ``updateDeviceState(deviceId, patch)`` for every change so
 *      the WebSocket ``device.state_changed`` round-trips back.
 *    - Closes on backdrop click, Escape key, or the close button.
 */
export interface DeviceDetailModalProps {
  deviceId: string | null;
  onClose: () => void;
}

export default function DeviceDetailModal({ deviceId, onClose }: DeviceDetailModalProps) {
  const { t } = useTranslation();
  const device = useStore((s) =>
    deviceId ? s.devices.find((d) => d.device_id === deviceId) ?? null : null,
  );
  const updateDeviceState = useStore((s) => s.updateDeviceState);

  // Close on Escape — standard modal hygiene. Also closes if `deviceId`
  // becomes null externally (parent unmounts the trigger row).
  useEffect(() => {
    if (!deviceId) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose();
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [deviceId, onClose]);

  return (
    <AnimatePresence>
      {deviceId && device && (
        <motion.div
          key="backdrop"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.18 }}
          onClick={onClose}
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(0, 0, 0, .45)',
            zIndex: 1000,
            display: 'flex',
            alignItems: 'flex-end',
            justifyContent: 'center',
          }}
        >
          <motion.div
            key="sheet"
            initial={{ y: '100%' }}
            animate={{ y: 0 }}
            exit={{ y: '100%' }}
            transition={{ duration: 0.25, ease: [0.5, 1.4, 0.5, 1] }}
            onClick={(e) => e.stopPropagation()}
            style={{
              width: '100%',
              maxWidth: 520,
              maxHeight: '80vh',
              overflowY: 'auto',
              background: 'var(--bg)',
              borderTopLeftRadius: 16,
              borderTopRightRadius: 16,
              border: '1px solid var(--b)',
              borderBottom: 'none',
              padding: '14px 18px 22px',
              display: 'flex',
              flexDirection: 'column',
              gap: 14,
              boxShadow: '0 -10px 40px rgba(0,0,0,.35)',
            }}
          >
            {/* Header — drag handle + name + close */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <div
                aria-hidden
                style={{
                  alignSelf: 'center',
                  width: 36, height: 4,
                  borderRadius: 2,
                  background: 'var(--tx3)',
                  opacity: 0.4,
                  marginBottom: 4,
                }}
              />
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <Icon
                  name={entityIconForFrontend(device.entity_type)}
                  size={22}
                  fallback="•"
                />
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{
                    fontSize: 16, fontWeight: 600, color: 'var(--tx)',
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  }}>
                    {device.name}
                  </div>
                  {(device.room ?? device.location) && (
                    <div style={{ fontSize: 11, color: 'var(--tx3)', marginTop: 2 }}>
                      {device.room ?? device.location}
                    </div>
                  )}
                </div>
                <button
                  onClick={onClose}
                  aria-label={t('common.close', { defaultValue: 'Close' })}
                  style={{
                    width: 32, height: 32,
                    borderRadius: 8,
                    background: 'var(--sf2)',
                    border: '1px solid var(--b)',
                    color: 'var(--tx2)',
                    cursor: 'pointer',
                    fontSize: 18, lineHeight: 1,
                  }}
                >
                  ×
                </button>
              </div>
            </div>

            {/* Capabilities stack */}
            <CapabilityList
              device={device}
              onPatch={(patch) => updateDeviceState(device.device_id, patch)}
              t={t}
            />
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

// ── Capability rendering ────────────────────────────────────────────────────

type T = (key: string, opts?: Record<string, unknown>) => string;

/** Walks the device's capabilities + state keys and emits one control
 *  per recognised parameter. Order is curated (on/off first, then
 *  brightness/color, then climate-specific). Unknown capabilities are
 *  silently dropped — the modal stays minimal rather than rendering
 *  raw JSON for things it can't drive. */
function CapabilityList({
  device, onPatch, t,
}: {
  device: Device;
  onPatch: (patch: Record<string, unknown>) => Promise<void> | void;
  t: T;
}) {
  const caps = new Set(device.capabilities ?? []);
  const state = device.state ?? {};

  // Heuristic: a state key implies the corresponding control even if
  // the explicit capability isn't declared. Catches drivers that
  // forget to advertise capabilities but still report state.
  const has = (key: string, stateKey?: string) =>
    caps.has(key) || (stateKey !== undefined && stateKey in state);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {(has('on') || 'on' in state) && (
        <OnOffControl state={state} onPatch={onPatch} t={t} />
      )}
      {('brightness' in state) && (
        <SliderControl
          label={t('deviceDetail.brightness', { defaultValue: 'Brightness' })}
          value={Number(state.brightness ?? 0)}
          min={0} max={255} step={1}
          unitFmt={(v) => `${Math.round((v / 255) * 100)}%`}
          onChange={(v) => onPatch({ brightness: v })}
        />
      )}
      {('color_temp' in state) && (
        <SliderControl
          label={t('deviceDetail.colorTemp', { defaultValue: 'Color temperature' })}
          value={Number(state.color_temp ?? 4000)}
          min={2200} max={6500} step={100}
          unitFmt={(v) => `${v}K`}
          onChange={(v) => onPatch({ color_temp: v })}
        />
      )}
      {has('set_mode', 'mode') && typeof state.mode === 'string' && (
        <SegmentedControl
          label={t('deviceDetail.mode', { defaultValue: 'Mode' })}
          options={[
            { id: 'auto', label: t('widgets.climate.modeAuto', { defaultValue: 'Auto' }) },
            { id: 'cool', label: t('widgets.climate.modeCool', { defaultValue: 'Cool' }) },
            { id: 'heat', label: t('widgets.climate.modeHeat', { defaultValue: 'Heat' }) },
            { id: 'dry',  label: t('widgets.climate.modeDry',  { defaultValue: 'Dry'  }) },
          ]}
          current={state.mode}
          onSelect={(id) => onPatch({ mode: id })}
        />
      )}
      {('target_temp' in state) && typeof state.target_temp === 'number' && (
        <StepperControl
          label={t('deviceDetail.targetTemp', { defaultValue: 'Target temperature' })}
          value={state.target_temp}
          min={16} max={30} step={0.5}
          unit="°"
          onChange={(v) => onPatch({ target_temp: v })}
        />
      )}
      {has('set_fan_speed', 'fan_speed') && typeof state.fan_speed === 'string' && (
        <SegmentedControl
          label={t('deviceDetail.fanSpeed', { defaultValue: 'Fan speed' })}
          options={[
            { id: 'auto', label: t('deviceDetail.fanAuto', { defaultValue: 'Auto' }) },
            { id: 'low',  label: t('deviceDetail.fanLow',  { defaultValue: 'Low'  }) },
            { id: 'med',  label: t('deviceDetail.fanMed',  { defaultValue: 'Med'  }) },
            { id: 'high', label: t('deviceDetail.fanHigh', { defaultValue: 'High' }) },
          ]}
          current={state.fan_speed}
          onSelect={(id) => onPatch({ fan_speed: id })}
        />
      )}
      {('volume' in state) && (
        <SliderControl
          label={t('deviceDetail.volume', { defaultValue: 'Volume' })}
          value={Number(state.volume ?? 0)}
          min={0} max={100} step={5}
          unitFmt={(v) => `${v}%`}
          onChange={(v) => onPatch({ volume: v })}
        />
      )}

      {/* Climate flag bank — boolean toggles for sleep / turbo / quiet etc.
          Rendered as a compact 2-column grid because there are typically
          several and individually they don't need much vertical space. */}
      {Object.keys(CLIMATE_FLAGS).some((k) => k in state) && (
        <FlagGrid state={state} onPatch={onPatch} t={t} />
      )}
    </div>
  );
}

const CLIMATE_FLAGS: Record<string, string> = {
  sleep: 'deviceDetail.flagSleep',
  turbo: 'deviceDetail.flagTurbo',
  quiet: 'deviceDetail.flagQuiet',
  eco: 'deviceDetail.flagEco',
  health: 'deviceDetail.flagHealth',
  light: 'deviceDetail.flagLight',
};

function FlagGrid({
  state, onPatch, t,
}: {
  state: Record<string, unknown>;
  onPatch: (patch: Record<string, unknown>) => Promise<void> | void;
  t: T;
}) {
  const flags = Object.keys(CLIMATE_FLAGS).filter((k) => k in state);
  if (flags.length === 0) return null;
  return (
    <div>
      <div style={{
        fontSize: 9.5, fontWeight: 600, color: 'var(--tx3)',
        textTransform: 'uppercase', letterSpacing: '.08em', marginBottom: 6,
      }}>
        {t('deviceDetail.flags', { defaultValue: 'Modes' })}
      </div>
      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 6,
      }}>
        {flags.map((k) => {
          const on = Boolean(state[k]);
          return (
            <button
              key={k}
              onClick={() => onPatch({ [k]: !on })}
              style={{
                padding: '8px 10px',
                borderRadius: 8,
                background: on ? 'color-mix(in srgb, var(--ac) 14%, var(--sf))' : 'var(--sf)',
                border: `1px solid ${on ? 'var(--ac)' : 'var(--b)'}`,
                color: 'var(--tx)',
                cursor: 'pointer',
                fontSize: 12,
                fontWeight: 500,
                textAlign: 'left',
                boxShadow: on ? 'var(--widget-glow-on)' : 'none',
              }}
            >
              {t(CLIMATE_FLAGS[k], { defaultValue: k })}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function OnOffControl({
  state, onPatch, t,
}: {
  state: Record<string, unknown>;
  onPatch: (patch: Record<string, unknown>) => Promise<void> | void;
  t: T;
}) {
  const on = Boolean(state.on) || state.power === 'on';
  return (
    <ControlRow label={t('deviceDetail.power', { defaultValue: 'Power' })}>
      <button
        onClick={() => onPatch({ on: !on })}
        style={{
          padding: '10px 18px',
          borderRadius: 10,
          background: on ? 'var(--ac)' : 'var(--sf2)',
          color: on ? 'var(--on-accent)' : 'var(--tx2)',
          border: `1px solid ${on ? 'var(--ac)' : 'var(--b)'}`,
          cursor: 'pointer',
          fontSize: 13,
          fontWeight: 600,
          minWidth: 84,
          boxShadow: on ? 'var(--widget-glow-on)' : 'none',
          transition: 'all .15s',
        }}
      >
        {on
          ? t('common.on', { defaultValue: 'On' })
          : t('common.off', { defaultValue: 'Off' })}
      </button>
    </ControlRow>
  );
}

function SliderControl({
  label, value, min, max, step, unitFmt, onChange,
}: {
  label: string;
  value: number;
  min: number; max: number; step: number;
  unitFmt: (v: number) => string;
  onChange: (v: number) => void;
}) {
  // Local state lets the slider update fluidly during drag without
  // racing the round-trip back through the store. We commit on
  // pointerup; transient values stay client-only.
  const [local, setLocal] = useState(value);
  useEffect(() => { setLocal(value); }, [value]);
  return (
    <ControlRow label={label} value={unitFmt(local)}>
      <input
        type="range"
        min={min} max={max} step={step}
        value={local}
        onChange={(e) => setLocal(Number(e.target.value))}
        onPointerUp={() => onChange(local)}
        onTouchEnd={() => onChange(local)}
        style={{ flex: 1, accentColor: 'var(--ac)', cursor: 'pointer' }}
      />
    </ControlRow>
  );
}

function StepperControl({
  label, value, min, max, step, unit, onChange,
}: {
  label: string;
  value: number;
  min: number; max: number; step: number;
  unit?: string;
  onChange: (v: number) => void;
}) {
  const dec = () => onChange(Math.max(min, value - step));
  const inc = () => onChange(Math.min(max, value + step));
  return (
    <ControlRow label={label}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <StepperButton onClick={dec} glyph="−" />
        <span style={{
          fontSize: 18, fontWeight: 500, color: 'var(--tx)',
          minWidth: 56, textAlign: 'center', fontVariantNumeric: 'tabular-nums',
        }}>
          {value.toFixed(1)}{unit}
        </span>
        <StepperButton onClick={inc} glyph="+" />
      </div>
    </ControlRow>
  );
}

function StepperButton({ onClick, glyph }: { onClick: () => void; glyph: string }) {
  return (
    <button
      onClick={onClick}
      style={{
        width: 32, height: 32, borderRadius: 8,
        background: 'var(--sf2)', border: '1px solid var(--b)',
        color: 'var(--tx)', cursor: 'pointer',
        fontSize: 16, fontWeight: 500, lineHeight: 1, padding: 0,
      }}
    >
      {glyph}
    </button>
  );
}

function SegmentedControl({
  label, options, current, onSelect,
}: {
  label: string;
  options: { id: string; label: string }[];
  current: string;
  onSelect: (id: string) => void;
}) {
  return (
    <ControlRow label={label} stack>
      <div style={{
        display: 'grid',
        gridTemplateColumns: `repeat(${options.length}, 1fr)`,
        gap: 4, padding: 3, borderRadius: 10,
        background: 'var(--sf2)', border: '1px solid var(--b)',
      }}>
        {options.map((o) => {
          const active = o.id === current;
          return (
            <button
              key={o.id}
              onClick={() => onSelect(o.id)}
              style={{
                padding: '8px 6px',
                fontSize: 11.5, fontWeight: 600,
                background: active ? 'var(--bg)' : 'transparent',
                color: active ? 'var(--tx)' : 'var(--tx2)',
                border: 'none',
                borderRadius: 7,
                cursor: 'pointer',
                boxShadow: active ? 'var(--shadow)' : 'none',
              }}
            >
              {o.label}
            </button>
          );
        })}
      </div>
    </ControlRow>
  );
}

function ControlRow({
  label, value, children, stack,
}: {
  label: string;
  value?: string;
  children: React.ReactNode;
  stack?: boolean;
}) {
  return (
    <div style={{
      display: 'flex',
      flexDirection: stack ? 'column' : 'row',
      alignItems: stack ? 'stretch' : 'center',
      justifyContent: 'space-between',
      gap: stack ? 6 : 12,
    }}>
      <div style={{
        fontSize: 11, color: 'var(--tx2)', fontWeight: 500,
        display: 'flex', justifyContent: 'space-between', alignItems: 'baseline',
      }}>
        <span>{label}</span>
        {value && (
          <span style={{
            color: 'var(--tx3)', fontVariantNumeric: 'tabular-nums', fontSize: 10.5,
          }}>
            {value}
          </span>
        )}
      </div>
      {!stack
        ? <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 1, justifyContent: 'flex-end' }}>{children}</div>
        : children}
    </div>
  );
}
