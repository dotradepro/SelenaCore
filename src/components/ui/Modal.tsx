import { useEffect } from 'react';
import type { ReactNode, MouseEvent } from 'react';

export interface ModalProps {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  width?: number | string;
  closeOnOverlayClick?: boolean;
  closeOnEscape?: boolean;
}

export function Modal({
  open,
  onClose,
  title,
  actions,
  children,
  width,
  closeOnOverlayClick = true,
  closeOnEscape = true,
}: ModalProps) {
  useEffect(() => {
    if (!open || !closeOnEscape) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose();
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, closeOnEscape, onClose]);

  if (!open) return null;

  function onOverlay(e: MouseEvent<HTMLDivElement>) {
    if (closeOnOverlayClick && e.target === e.currentTarget) onClose();
  }

  return (
    <div className="modal-overlay" onClick={onOverlay}>
      <div className="modal" style={width != null ? { width, maxWidth: width } : undefined}>
        {title != null && <div className="modal-title">{title}</div>}
        {children}
        {actions != null && <div className="modal-actions">{actions}</div>}
      </div>
    </div>
  );
}
