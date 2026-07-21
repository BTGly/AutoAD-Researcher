import { useEffect, type RefObject } from 'react';

export function useDialogFocus<T extends HTMLElement>(initialFocusRef: RefObject<T | null>) {
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
}
