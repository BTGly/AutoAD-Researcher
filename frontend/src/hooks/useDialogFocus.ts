import { useEffect, type RefObject } from 'react';

interface DialogOptions {
  dialogRef?: RefObject<HTMLElement | null>;
  onClose?: () => void;
}

const FOCUSABLE_SELECTOR = 'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function useDialogFocus<T extends HTMLElement>(initialFocusRef: RefObject<T | null>, { dialogRef, onClose }: DialogOptions = {}) {
  useEffect(() => {
    const returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const focusTimer = window.setTimeout(() => initialFocusRef.current?.focus(), 0);

    return () => {
      window.clearTimeout(focusTimer);
      if (returnFocus?.isConnected) {
        window.requestAnimationFrame(() => returnFocus.focus());
      }
    };
  }, [initialFocusRef]);

  useEffect(() => {
    const dialog = dialogRef?.current;
    if (!dialog) return;

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && onClose) {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== 'Tab') return;
      const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(element => element.offsetParent !== null);
      if (focusable.length === 0) {
        event.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    dialog.addEventListener('keydown', handleKeyDown);
    return () => dialog.removeEventListener('keydown', handleKeyDown);
  }, [dialogRef, onClose]);
}
