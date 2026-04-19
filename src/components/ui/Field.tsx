import type { ReactNode } from 'react';

export interface FieldProps {
  label?: ReactNode;
  hint?: ReactNode;
  htmlFor?: string;
  className?: string;
  children: ReactNode;
}

export function Field({ label, hint, htmlFor, className, children }: FieldProps) {
  const cls = ['field', className].filter(Boolean).join(' ');
  return (
    <div className={cls}>
      {label != null && <label htmlFor={htmlFor}>{label}</label>}
      {children}
      {hint != null && <div className="hint">{hint}</div>}
    </div>
  );
}

export interface FieldRowProps {
  className?: string;
  children: ReactNode;
}

export function FieldRow({ className, children }: FieldRowProps) {
  return <div className={['field-row', className].filter(Boolean).join(' ')}>{children}</div>;
}
