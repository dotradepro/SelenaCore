import { forwardRef } from 'react';
import type { ButtonHTMLAttributes, ReactNode } from 'react';

type Variant =
  | 'primary'
  | 'secondary'
  | 'danger'
  | 'danger-solid'
  | 'amber'
  | 'amber-solid'
  | 'green'
  | 'ghost'
  | 'outline'
  | 'link';

type Size = 'xs' | 'sm' | 'md';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  leftIcon?: ReactNode;
  rightIcon?: ReactNode;
}

const VARIANT_CLASS: Record<Variant, string> = {
  primary: 'btn-primary',
  secondary: 'btn-secondary',
  danger: 'btn-danger-tonal',
  'danger-solid': 'btn-danger-solid',
  amber: 'btn-amber',
  'amber-solid': 'btn-amber-solid',
  green: 'btn-green',
  ghost: 'btn-ghost',
  outline: 'btn-outline',
  link: 'btn-link',
};

const SIZE_CLASS: Record<Size, string> = {
  xs: 'btn-xs',
  sm: 'btn-sm',
  md: '',
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'secondary', size = 'md', loading, leftIcon, rightIcon, className, children, disabled, type = 'button', ...rest },
  ref,
) {
  const cls = [
    'btn',
    VARIANT_CLASS[variant],
    SIZE_CLASS[size],
    loading ? 'btn-loading' : '',
    className,
  ]
    .filter(Boolean)
    .join(' ');
  return (
    <button ref={ref} type={type} className={cls} disabled={disabled || loading} {...rest}>
      {leftIcon}
      {children}
      {rightIcon}
    </button>
  );
});
