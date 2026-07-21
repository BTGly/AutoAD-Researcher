import { useState } from 'react';
import type { AppConfig } from '../hooks/useConfig';
import { AppButton } from './ui/AppButton';
import { Surface } from './ui/Surface';

interface Props {
  onSave: (c: AppConfig) => Promise<void>;
}

export function FirstRunSetup({ onSave }: Props) {
  const [key, setKey] = useState('');
  const [url, setUrl] = useState('https://api.deepseek.com');
  const [model, setModel] = useState('deepseek-v4-flash');
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    if (!key.trim()) return;
    setSaving(true);
    await onSave({ apiKey: key.trim(), baseUrl: url.trim(), model: model.trim() });
  };

  return (
    <div style={{
      height: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: 'var(--bg)', color: 'var(--text)',
    }}>
      <Surface style={{
        width: 420, maxWidth: '90vw', padding: 40,
      }}>
        <div style={{ fontSize: '1.4em', fontWeight: 600, color: 'var(--blue)', marginBottom: 8 }}>
          AutoAD Researcher
        </div>
        <div style={{ color: 'var(--text-muted)', marginBottom: 28, fontSize: '0.9em' }}>
          面向异常检测科研任务的 Agent 工作台<br />
          API Key 保存在本设备浏览器，不上传服务器。
        </div>

        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: '0.8em', color: 'var(--text-muted)', marginBottom: 4 }}>API Key</div>
          <input type="password" value={key} onChange={e => setKey(e.target.value)} placeholder="sk-…" autoFocus />
        </div>
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: '0.8em', color: 'var(--text-muted)', marginBottom: 4 }}>Base URL</div>
          <input value={url} onChange={e => setUrl(e.target.value)} placeholder="https://api.deepseek.com" />
        </div>
        <div style={{ marginBottom: 24 }}>
          <div style={{ fontSize: '0.8em', color: 'var(--text-muted)', marginBottom: 4 }}>Model</div>
          <input value={model} onChange={e => setModel(e.target.value)} placeholder="deepseek-v4-flash" />
        </div>

        <AppButton
          variant="primary"
          disabled={!key.trim() || saving}
          onClick={handleSave}
          style={{ width: '100%', padding: '10px 0', fontSize: '0.95em' }}
        >
          {saving ? '正在创建任务…' : '保存并创建任务'}
        </AppButton>
      </Surface>
    </div>
  );
}
