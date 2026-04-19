export interface SpinnerProps {
  className?: string;
  size?: number;
}

export function Spinner({ className, size }: SpinnerProps) {
  if (size != null) {
    return (
      <span
        className={['spinner', className].filter(Boolean).join(' ')}
        style={{ width: size, height: size, borderWidth: Math.max(2, Math.round(size / 6)) }}
      />
    );
  }
  return <span className={['spinner', className].filter(Boolean).join(' ')} />;
}
