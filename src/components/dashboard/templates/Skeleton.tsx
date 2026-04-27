/** Pulsing-block skeleton matched to the structural shape of each template.
 *  Uses the `--skeleton-bg` token (defined in index.css §2.6 of the recraft
 *  doc) so the animation respects light/dark themes without inline overrides. */
import type { CSSProperties } from 'react';

const baseBlock: CSSProperties = {
  background: 'var(--skeleton-bg)',
  backgroundSize: '200% 100%',
  borderRadius: 8,
  animation: 'skeletonShimmer 1.4s linear infinite',
};

const headerBlock: CSSProperties = {
  ...baseBlock,
  height: 10,
  width: '40%',
  marginBottom: 10,
};

export function MetricSkeleton() {
  return (
    <Container>
      <div style={headerBlock} />
      <div style={{ ...baseBlock, height: 28, width: '55%', marginTop: 6 }} />
      <div style={{ ...baseBlock, height: 9, width: '35%', marginTop: 8 }} />
    </Container>
  );
}

export function ToggleListSkeleton() {
  return (
    <Container>
      <div style={headerBlock} />
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 6 }}>
        {[0, 1, 2, 3].map((i) => (
          <div key={i} style={{ ...baseBlock, height: 24 }} />
        ))}
      </div>
    </Container>
  );
}

export function SparklineSkeleton() {
  return (
    <Container>
      <div style={headerBlock} />
      <div style={{ ...baseBlock, height: 22, width: '40%', marginTop: 4 }} />
      <div style={{ ...baseBlock, height: 32, width: '100%', marginTop: 10, borderRadius: 4 }} />
    </Container>
  );
}

export function ControlPanelSkeleton() {
  return (
    <Container>
      <div style={headerBlock} />
      <div style={{ ...baseBlock, height: 28, width: '40%', marginTop: 4 }} />
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 4, marginTop: 10 }}>
        {[0, 1, 2, 3].map((i) => <div key={i} style={{ ...baseBlock, height: 24 }} />)}
      </div>
      <div style={{ ...baseBlock, height: 24, width: '100%', marginTop: 8 }} />
    </Container>
  );
}

export function StatusSkeleton() {
  return (
    <Container>
      <div style={headerBlock} />
      <div style={{ ...baseBlock, height: 18, width: '50%', marginTop: 4, borderRadius: 999 }} />
      <div style={{ ...baseBlock, height: 9, width: '80%', marginTop: 10 }} />
      <div style={{ ...baseBlock, height: 9, width: '60%', marginTop: 4 }} />
    </Container>
  );
}

export function GenericSkeleton() {
  return (
    <Container>
      <div style={headerBlock} />
      <div style={{ ...baseBlock, height: 16, width: '70%', marginTop: 6 }} />
      <div style={{ ...baseBlock, height: 16, width: '50%', marginTop: 6 }} />
    </Container>
  );
}

function Container({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ width: '100%', height: '100%', padding: 12 }}>
      {children}
    </div>
  );
}

/** Adds the keyframes once globally — components import this side-effect-free. */
if (typeof document !== 'undefined' && !document.getElementById('selena-skeleton-kf')) {
  const s = document.createElement('style');
  s.id = 'selena-skeleton-kf';
  s.textContent = `@keyframes skeletonShimmer { 0% { background-position: 100% 0 } 100% { background-position: -100% 0 } }`;
  document.head.appendChild(s);
}
