import { forwardRef } from 'react';
import type { InputHTMLAttributes } from 'react';

export interface ToggleProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type' | 'size'> {
  size?: 'sm' | 'md';
  variant?: 'green' | 'amber';
  label?: string;
}

export const Toggle = forwardRef<HTMLInputElement, ToggleProps>(function Toggle(
  { size = 'md', variant = 'green', label, className, id, ...rest },
  ref,
) {
  const labelCls = [
    'toggle',
    size === 'sm' ? 'toggle-sm' : '',
    variant === 'amber' ? 'amber' : '',
    className,
  ]
    .filter(Boolean)
    .join(' ');
  return (
    <label className={labelCls} aria-label={label} htmlFor={id}>
      <input ref={ref} id={id} type="checkbox" {...rest} />
      <span className="slider" />
    </label>
  );
});
