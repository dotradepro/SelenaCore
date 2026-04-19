import { forwardRef } from 'react';
import type { InputHTMLAttributes, TextareaHTMLAttributes, SelectHTMLAttributes } from 'react';

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { className, type = 'text', ...rest },
  ref,
) {
  return (
    <input
      ref={ref}
      type={type}
      className={['form-control', className].filter(Boolean).join(' ')}
      {...rest}
    />
  );
});

export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { className, rows = 4, ...rest },
  ref,
) {
  return (
    <textarea
      ref={ref}
      rows={rows}
      className={['form-control', className].filter(Boolean).join(' ')}
      {...rest}
    />
  );
});

export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { className, children, ...rest },
  ref,
) {
  return (
    <select
      ref={ref}
      className={['form-control', className].filter(Boolean).join(' ')}
      {...rest}
    >
      {children}
    </select>
  );
});
