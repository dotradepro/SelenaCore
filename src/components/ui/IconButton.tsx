import { forwardRef } from 'react';
import type { ButtonHTMLAttributes, ReactNode } from 'react';

type Tone = 'default' | 'danger' | 'success';
type Size = 'sm' | 'md' | 'lg';

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  tone?: Tone;
  size?: Size;
  icon: ReactNode;
  label: string;
}

const SIZE_CLASS: Record<Size, string> = { sm: 'icon-btn-sm', md: '', lg: 'icon-btn-lg' };

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { tone = 'default', size = 'md', icon, label, className, type = 'button', ...rest },
  ref,
) {
  const cls = [
    'icon-btn',
    SIZE_CLASS[size],
    tone === 'danger' ? 'danger' : tone === 'success' ? 'success' : '',
    className,
  ]
    .filter(Boolean)
    .join(' ');
  return (
    <button ref={ref} type={type} className={cls} aria-label={label} title={label} {...rest}>
      {icon}
    </button>
  );
});
