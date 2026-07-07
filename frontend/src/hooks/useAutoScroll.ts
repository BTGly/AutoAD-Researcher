import { useRef, useEffect } from 'react';

export function useAutoScroll(deps: unknown[]) {
  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, deps);
  return bottomRef;
}
