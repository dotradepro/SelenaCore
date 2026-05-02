import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { Device } from '../../../store/useStore';

/** Reusable per-device capability controls. Used by the toggle-list
 *  drill-in view (single click on a device tile expands it inside the
 *  same widget) and any other surface that needs to expose every
 *  parameter of a device.
 *
 *  Walks the device's capabilities + state keys and emits one control
 *  per recognised parameter. Order is curated (power first, then
 *  brightness/color, then climate-specific, then volume, then climate
 *  flag bank). Unknown capabilities are silently dropped — better to
 *  show a minimal panel than leak raw JSON for things we can't drive.
 *
 *  Sliders use local state during drag and commit on pointer-up so
 *  the UI doesn't fight the WebSocket round-trip.
 */
export interface DeviceCapabilitiesProps {
  device: Device;
  onChange: (patch: Record<string, unknown>) => Promise<void> | void;
  /** Compact layout collapses gaps + smaller controls so the panel
   *  fits inside a small widget cell. Defaults to ``true`` since the
   *  primary call-site is inline in a 4×2 toggle-list widget. */
  compact?: boolean;
}

type T = (key: string, opts?: Record<string, unknown>) => string;

export default function DeviceCapabilities({
  device, onChange, compact = true,
}: DeviceCapabilitiesProps) {
  const { t } = useTranslation();
  const caps = new Set(device.capabilities ?? []);
  const state = device.state ?? {};

  // Heuristic: a state key implies the corresponding control even if
  // the explicit capability isn't declared. Catches drivers that
  // forget to advertise capabilities but still report state.
  const has = (cap: string, stateKey?: string) =>
    caps.has(cap) || (stateKey !== undefined && stateKey in state);

  const gap = compact ? 8 : 12;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap }}>
      {(has('on') || 'on' in state) && (
        <OnOffControl state={state} onPatch={onChange} t={t} compact={compact} />
      )}
      {('brightness' in state) && (
        <SliderControl
          label={t('deviceDetail.brightness', { defaultValue: 'Brightness' })}
          value={Number(state.brightness ?? 0)}
          min={0} max={255} step={1}
          unitFmt={(v) => `${Math.round((v / 255) * 100)}%`}
          onChange={(v) => onChange({ brightness: v })}
          compact={compact}
        />
      )}
      {('color_temp' in state) && (
        <SliderControl
          label={t('deviceDetail.colorTemp', { defaultValue: 'Color temperature' })}
          value={Number(state.color_temp ?? 4000)}
          min={2200} max={6500} step={100}
          unitFmt={(v) => `${v}K`}
          onChange={(v) => onChange({ color_temp: v })}
          compact={compact}
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
          onSelect={(id) => onChange({ mode: id })}
          compact={compact}
        />
      )}
      {('target_temp' in state) && typeof state.target_temp === 'number' && (
        <StepperControl
          label={t('deviceDetail.targetTemp', { defaultValue: 'Target temperature' })}
          value={state.target_temp}
          min={16} max={30} step={0.5}
          unit="°"
          onChange={(v) => onChange({ target_temp: v })}
          compact={compact}
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
          onSelect={(id) => onChange({ fan_speed: id })}
          compact={compact}
        />
      )}
      {('volume' in state) && (
        <SliderControl
          label={t('deviceDetail.volume', { defaultValue: 'Volume' })}
          value={Number(state.volume ?? 0)}
          min={0} max={100} step={5}
          unitFmt={(v) => `${v}%`}
          onChange={(v) => onChange({ volume: v })}
          compact={compact}
        />
      )}

      {/* Climate flag bank — boolean toggles for sleep / turbo / quiet etc. */}
      {Object.keys(CLIMATE_FLAGS).some((k) => k in state) && (
        <FlagGrid state={state} onPatch={onChange} t={t} />
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
                padding: '6px 10px',
                borderRadius: 8,
                background: on ? 'color-mix(in srgb, var(--ac) 14%, var(--sf))' : 'var(--sf)',
                border: `1px solid ${on ? 'var(--ac)' : 'var(--b)'}`,
                color: 'var(--tx)',
                cursor: 'pointer',
                fontSize: 11,
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
  state, onPatch, t, compact,
}: {
  state: Record<string, unknown>;
  onPatch: (patch: Record<string, unknown>) => Promise<void> | void;
  t: T;
  compact: boolean;
}) {
  const on = Boolean(state.on) || state.power === 'on';
  return (
    <ControlRow label={t('deviceDetail.power', { defaultValue: 'Power' })} compact={compact}>
      <button
        onClick={() => onPatch({ on: !on })}
        style={{
          padding: compact ? '6px 14px' : '10px 18px',
          borderRadius: 10,
          background: on ? 'var(--ac)' : 'var(--sf2)',
          color: on ? 'var(--on-accent)' : 'var(--tx2)',
          border: `1px solid ${on ? 'var(--ac)' : 'var(--b)'}`,
          cursor: 'pointer',
          fontSize: compact ? 12 : 13,
          fontWeight: 600,
          minWidth: compact ? 64 : 84,
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
  label, value, min, max, step, unitFmt, onChange, compact,
}: {
  label: string;
  value: number;
  min: number; max: number; step: number;
  unitFmt: (v: number) => string;
  onChange: (v: number) => void;
  compact: boolean;
}) {
  const [local, setLocal] = useState(value);
  useEffect(() => { setLocal(value); }, [value]);
  return (
    <ControlRow label={label} value={unitFmt(local)} compact={compact}>
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
  label, value, min, max, step, unit, onChange, compact,
}: {
  label: string;
  value: number;
  min: number; max: number; step: number;
  unit?: string;
  onChange: (v: number) => void;
  compact: boolean;
}) {
  const dec = () => onChange(Math.max(min, value - step));
  const inc = () => onChange(Math.min(max, value + step));
  const btnSize = compact ? 26 : 32;
  return (
    <ControlRow label={label} compact={compact}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <StepperButton onClick={dec} glyph="−" size={btnSize} />
        <span style={{
          fontSize: compact ? 14 : 18, fontWeight: 500, color: 'var(--tx)',
          minWidth: 50, textAlign: 'center', fontVariantNumeric: 'tabular-nums',
        }}>
          {value.toFixed(1)}{unit}
        </span>
        <StepperButton onClick={inc} glyph="+" size={btnSize} />
      </div>
    </ControlRow>
  );
}

function StepperButton({ onClick, glyph, size }: {
  onClick: () => void; glyph: string; size: number;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        width: size, height: size, borderRadius: 8,
        background: 'var(--sf2)', border: '1px solid var(--b)',
        color: 'var(--tx)', cursor: 'pointer',
        fontSize: 14, fontWeight: 500, lineHeight: 1, padding: 0,
      }}
    >
      {glyph}
    </button>
  );
}

function SegmentedControl({
  label, options, current, onSelect, compact,
}: {
  label: string;
  options: { id: string; label: string }[];
  current: string;
  onSelect: (id: string) => void;
  compact: boolean;
}) {
  return (
    <ControlRow label={label} stack compact={compact}>
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
                padding: compact ? '5px 4px' : '8px 6px',
                fontSize: 11, fontWeight: 600,
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
  label, value, children, stack, compact,
}: {
  label: string;
  value?: string;
  children: React.ReactNode;
  stack?: boolean;
  compact: boolean;
}) {
  return (
    <div style={{
      display: 'flex',
      flexDirection: stack ? 'column' : 'row',
      alignItems: stack ? 'stretch' : 'center',
      justifyContent: 'space-between',
      gap: stack ? 4 : (compact ? 8 : 12),
    }}>
      <div style={{
        fontSize: compact ? 10.5 : 11, color: 'var(--tx2)', fontWeight: 500,
        display: 'flex', justifyContent: 'space-between', alignItems: 'baseline',
      }}>
        <span>{label}</span>
        {value && (
          <span style={{
            color: 'var(--tx3)', fontVariantNumeric: 'tabular-nums', fontSize: 10,
          }}>
            {value}
          </span>
        )}
      </div>
      {!stack
        ? <div style={{ display: 'flex', alignItems: 'center', gap: 6, flex: 1, justifyContent: 'flex-end' }}>{children}</div>
        : children}
    </div>
  );
}
