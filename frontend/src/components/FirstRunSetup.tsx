import { useState } from 'react';
import { MODEL_OPTIONS } from '../hooks/useConfig';
import type { AppConfig, ModelId } from '../hooks/useConfig';

interface Props {
  onSave: (c: AppConfig) => Promise<void>;
}

export function FirstRunSetup({ onSave }: Props) {
  const [key, setKey] = useState('');
  const [url, setUrl] = useState('https://api.deepseek.com');
  const [dialogueModel, setDialogueModel] = useState<ModelId>('deepseek-v4-flash');
  const [reportModel, setReportModel] = useState<ModelId>('deepseek-v4-flash');
  const [experimentModel, setExperimentModel] = useState<ModelId>('deepseek-v4-pro');
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    if (!key.trim()) return;
    setSaving(true);
    await onSave({ apiKey: key.trim(), baseUrl: url.trim(), dialogueModel, reportModel, experimentModel });
  };

  return (
    <div style={{
      height: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: 'var(--bg)', color: 'var(--text)',
    }}>
      <div style={{
        width: 420, maxWidth: '90vw', padding: 40,
        border: '1px solid var(--border)', borderRadius: 12,
        background: 'var(--bg-panel)',
      }}>
        <div style={{ fontSize: '1.4em', fontWeight: 600, color: 'var(--blue)', marginBottom: 8 }}>
          AutoAD Researcher
        </div>
        <div style={{ color: 'var(--text-muted)', marginBottom: 28, fontSize: '0.9em' }}>
          面向异常检测科研任务的 Agent 工作台<br />
          API Key 只保存在本设备浏览器，不写入项目记录；在线请求会发送到本地 AutoAD 服务，后台报告和实验使用服务端凭据。
        </div>

        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: '0.8em', color: 'var(--text-muted)', marginBottom: 4 }}>API Key</div>
          <input type="password" value={key} onChange={e => setKey(e.target.value)} placeholder="sk-…" autoFocus />
        </div>
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: '0.8em', color: 'var(--text-muted)', marginBottom: 4 }}>Base URL</div>
          <input value={url} onChange={e => setUrl(e.target.value)} placeholder="https://api.deepseek.com" />
        </div>
        <div style={{ display: 'grid', gap: 10, marginBottom: 24 }}>
          <ModelSelect label="对话模型" value={dialogueModel} onChange={setDialogueModel} />
          <ModelSelect label="报告模型" value={reportModel} onChange={setReportModel} />
          <ModelSelect label="实验 Agent 模型" value={experimentModel} onChange={setExperimentModel} />
        </div>

        <button
          className="primary"
          disabled={!key.trim() || saving}
          onClick={handleSave}
          style={{ width: '100%', padding: '10px 0', fontSize: '0.95em' }}
        >
          {saving ? '正在创建任务…' : '保存并创建任务'}
        </button>
      </div>
    </div>
  );
}

function ModelSelect({ label, value, onChange }: { label: string; value: ModelId; onChange: (value: ModelId) => void }) {
  return (
    <label style={{ display: 'block', fontSize: '0.8em', color: 'var(--text-muted)' }}>
      <span style={{ display: 'block', marginBottom: 4 }}>{label}</span>
      <select value={value} onChange={e => onChange(e.target.value as ModelId)} style={{ width: '100%' }}>
        {MODEL_OPTIONS.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select>
    </label>
  );
}
