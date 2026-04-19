import type { ReactNode } from 'react';

export interface EmptyStateProps {
  title?: ReactNode;
  className?: string;
  children?: ReactNode;
}

export function EmptyState({ title, className, children }: EmptyStateProps) {
  return (
    <div className={['empty-state', className].filter(Boolean).join(' ')}>
      {title != null && <div className="es-title">{title}</div>}
      {children}
    </div>
  );
}
