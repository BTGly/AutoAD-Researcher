import { useEffect, useState } from 'react';
import type { AppConfig } from '../hooks/useConfig';
import { AppButton } from './ui/AppButton';
import { Surface } from './ui/Surface';

interface Props {
  config: AppConfig;
  onSave: (c: AppConfig) => void;
  onClose: () => void;
}

export function ConfigModal({ config, onSave, onClose }: Props) {
  const [key, setKey] = useState(config.apiKey);
  const [url, setUrl] = useState(config.baseUrl);
  const [model, setModel] = useState(config.model);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-label="配置 API Key">
      <Surface className="modal">
        <h2 style={{ fontSize: '1.2em', marginBottom: 20, color: 'var(--blue)' }}>配置 API Key</h2>
        <p style={{ fontSize: '0.85em', color: 'var(--text-muted)', marginBottom: 16 }}>
          API Key 保存在本设备浏览器中，不上传服务器。
        </p>

        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: '0.8em', color: 'var(--text-muted)', marginBottom: 4 }}>API Key</div>
          <input autoFocus type="password" value={key} onChange={e => setKey(e.target.value)} placeholder="sk-…" />
        </div>

        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: '0.8em', color: 'var(--text-muted)', marginBottom: 4 }}>Base URL</div>
          <input value={url} onChange={e => setUrl(e.target.value)} placeholder="https://api.deepseek.com" />
        </div>

        <div style={{ marginBottom: 20 }}>
          <div style={{ fontSize: '0.8em', color: 'var(--text-muted)', marginBottom: 4 }}>Model</div>
          <input value={model} onChange={e => setModel(e.target.value)} placeholder="deepseek-v4-flash" />
        </div>

        <div style={{ display: 'flex', gap: 8 }}>
          <AppButton variant="primary" onClick={() => onSave({ apiKey: key, baseUrl: url, model })} disabled={!key.trim()} style={{ flex: 1 }}>
            保存并开始
          </AppButton>
          {config.apiKey && (
            <AppButton onClick={onClose} style={{ flex: 1 }}>
              取消
            </AppButton>
          )}
        </div>
      </Surface>
    </div>
  );
}
