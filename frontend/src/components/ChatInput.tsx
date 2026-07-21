import type { KeyboardEvent } from 'react';
import { AppButton } from './ui/AppButton';

interface Props {
  value: string;
  onChange: (value: string) => void;
  onSend: (text: string) => void;
  disabled?: boolean;
}

export function ChatInput({ value, onChange, onSend, disabled }: Props) {
  const send = () => {
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    onChange('');
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  return (
    <div className="chat-composer">
      <textarea
        value={value}
        onChange={e => onChange(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="输入问题，或粘贴 URL…"
        rows={1}
        disabled={disabled}
        className="chat-composer-input"
      />
      <AppButton variant="primary" onClick={send} disabled={disabled || !value.trim()}>
        发送
      </AppButton>
    </div>
  );
}
