import type { ReactNode } from 'react';

export interface CardProps {
  className?: string;
  children: ReactNode;
}

export function Card({ className, children }: CardProps) {
  return <div className={['card', className].filter(Boolean).join(' ')}>{children}</div>;
}

export interface CardHeaderProps {
  title?: ReactNode;
  actions?: ReactNode;
  className?: string;
}

export function CardHeader({ title, actions, className }: CardHeaderProps) {
  return (
    <div className={['card-header', className].filter(Boolean).join(' ')}>
      {title != null && <div className="card-title" style={{ marginBottom: 0 }}>{title}</div>}
      {actions}
    </div>
  );
}
