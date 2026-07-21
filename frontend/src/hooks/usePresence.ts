import { useEffect, useState } from 'react';

type PresenceState = 'open' | 'closed';

export function usePresence(open: boolean, exitDuration: number) {
  const [present, setPresent] = useState(open);
  const [state, setState] = useState<PresenceState>(open ? 'open' : 'closed');

  useEffect(() => {
    if (open) {
      setPresent(true);
      setState('open');
      return;
    }

    setState('closed');
    const timer = window.setTimeout(() => setPresent(false), exitDuration);
    return () => window.clearTimeout(timer);
  }, [exitDuration, open]);

  return { present, state };
}
