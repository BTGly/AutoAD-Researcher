import { useEffect, useRef, useCallback } from 'react';
import { wsUrl } from '../lib/api';
import type { WSMessage } from '../lib/types';

interface Props {
  runId: string;
  onMessage: (msg: WSMessage) => void;
  enabled?: boolean;
}

export function useWebSocket({ runId, onMessage, enabled = true }: Props) {
  const wsRef = useRef<WebSocket | null>(null);
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;

  const connect = useCallback(() => {
    if (!enabled || !runId || runId === 'run_default') return;
    try {
      const ws = new WebSocket(wsUrl(runId));
      ws.onopen = () => console.log('[ws] connected', runId);
      ws.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data) as WSMessage;
          onMessageRef.current(msg);
        } catch {}
      };
      ws.onclose = () => console.log('[ws] closed, reconnecting in 3s...');
      ws.onerror = () => ws.close();
      wsRef.current = ws;
    } catch {}
  }, [runId, enabled]);

  useEffect(() => {
    connect();
    const interval = setInterval(() => {
      if (!wsRef.current || wsRef.current.readyState === WebSocket.CLOSED) {
        connect();
      }
    }, 5000);
    return () => {
      clearInterval(interval);
      wsRef.current?.close();
    };
  }, [connect]);

  return wsRef;
}
