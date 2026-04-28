import Icon from '../Icon';

export interface CardSpec {
  title?: string;
  /** Larger primary value rendered below the title. */
  value: string;
  /** Optional secondary line under the value. */
  secondary?: string | null;
  icon?: string | null;
  /** Renders the secondary in a tone color so contrast (hot/cold, ok/warn)
   *  reads at a glance — used by weather forecast cards (high/low). */
  tone?: 'neutral' | 'info' | 'ok' | 'warn' | 'alert';
}

const TONE_COLOR: Record<NonNullable<CardSpec['tone']>, string> = {
  neutral: 'var(--tx2)',
  info: 'var(--ac)',
  ok: 'var(--gr)',
  warn: 'var(--am)',
  alert: 'var(--rd)',
};

export interface CardRowProps {
  cards: CardSpec[];
  /** Cap on visible cards; trailing items are hidden so the row never
   *  overflows a 4×2 widget cell. */
  max?: number;
}

/** Horizontal row of mini cards used by Weather (forecast), Sparkline
 *  (top contributors), and Status (recent items). Each card is icon +
 *  title + value + secondary. Cards split available width equally. */
export default function CardRow({ cards, max = 4 }: CardRowProps) {
  const visible = cards.slice(0, max);
  if (visible.length === 0) return null;
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: `repeat(${visible.length}, minmax(0, 1fr))`,
        gap: 6,
      }}
    >
      {visible.map((card, i) => (
        <div
          key={i}
          style={{
            background: 'var(--sf2)',
            border: '1px solid var(--b)',
            borderRadius: 8,
            padding: '6px 8px',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: 2,
          }}
        >
          {card.title && (
            <div style={{
              fontSize: 9, color: 'var(--tx3)',
              textTransform: 'uppercase', letterSpacing: '.04em',
            }}>
              {card.title}
            </div>
          )}
          {card.icon && <Icon name={card.icon} size={18} color="var(--tx)" />}
          <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--tx)' }}>
            {card.value}
          </div>
          {card.secondary && (
            <div style={{ fontSize: 9, color: TONE_COLOR[card.tone ?? 'neutral'] }}>
              {card.secondary}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
