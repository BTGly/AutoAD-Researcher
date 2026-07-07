import { useState, type KeyboardEvent } from 'react';

interface Props {
  onSend: (text: string) => void;
  disabled?: boolean;
}

export function ChatInput({ onSend, disabled }: Props) {
  const [text, setText] = useState('');

  const send = () => {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setText('');
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  return (
    <div style={{ display: 'flex', gap: 8, padding: '8px 0', borderTop: '1px solid var(--border)' }}>
      <textarea
        value={text}
        onChange={e => setText(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="输入问题，或粘贴 URL…"
        rows={1}
        disabled={disabled}
        style={{ flex: 1, resize: 'none', maxHeight: 120, minHeight: 38 }}
      />
      <button onClick={send} disabled={disabled || !text.trim()} className="primary" style={{ padding: '6px 16px' }}>
        发送
      </button>
    </div>
  );
}
