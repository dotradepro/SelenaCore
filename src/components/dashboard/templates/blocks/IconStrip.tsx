import Icon from '../Icon';

export interface IconStripItem {
  icon?: string | null;
  value: string;
  /** Optional sub-label rendered under the value at smaller font size. */
  label?: string;
  tone?: 'neutral' | 'info' | 'ok' | 'warn' | 'alert';
}

const TONE_COLOR: Record<NonNullable<IconStripItem['tone']>, string> = {
  neutral: 'var(--tx2)',
  info: 'var(--ac)',
  ok: 'var(--gr)',
  warn: 'var(--am)',
  alert: 'var(--rd)',
};

export interface IconStripProps {
  items: IconStripItem[];
}

/** Compact horizontal strip of icon + value pairs. Replaces the ad-hoc
 *  pill rows in the V1 weather widget (humidity / wind / UV) and is used
 *  by status template's optional ``cards`` slot when only a single line
 *  of telemetry is needed. */
export default function IconStrip({ items }: IconStripProps) {
  if (!items || items.length === 0) return null;
  return (
    <div
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: '4px 10px',
        alignItems: 'center',
      }}
    >
      {items.map((item, i) => (
        <div
          key={i}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 4,
            color: TONE_COLOR[item.tone ?? 'neutral'],
            fontSize: 11,
          }}
        >
          {item.icon && <Icon name={item.icon} size={12} />}
          <span style={{ fontWeight: 500 }}>{item.value}</span>
          {item.label && (
            <span style={{ color: 'var(--tx3)', fontSize: 10 }}>{item.label}</span>
          )}
        </div>
      ))}
    </div>
  );
}
