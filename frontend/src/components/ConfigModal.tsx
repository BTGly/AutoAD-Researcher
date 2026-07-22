import { useState } from 'react';
import { MODEL_OPTIONS } from '../hooks/useConfig';
import type { AppConfig, ModelId } from '../hooks/useConfig';

interface Props {
  config: AppConfig;
  onSave: (c: AppConfig) => void;
  onClose: () => void;
}

export function ConfigModal({ config, onSave, onClose }: Props) {
  const [key, setKey] = useState(config.apiKey);
  const [url, setUrl] = useState(config.baseUrl);
  const [dialogueModel, setDialogueModel] = useState<ModelId>(config.dialogueModel);
  const [reportModel, setReportModel] = useState<ModelId>(config.reportModel);
  const [experimentModel, setExperimentModel] = useState<ModelId>(config.experimentModel);

  return (
    <div className="modal-overlay">
      <div className="modal">
        <h2 style={{ fontSize: '1.2em', marginBottom: 20, color: 'var(--blue)' }}>🔑 配置 API Key</h2>
        <p style={{ fontSize: '0.85em', color: 'var(--text-muted)', marginBottom: 16 }}>
          API Key 只保存在本设备浏览器，不写入项目记录；在线请求会发送到本地 AutoAD 服务，后台报告和实验使用服务端凭据。
        </p>

        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: '0.8em', color: 'var(--text-muted)', marginBottom: 4 }}>API Key</div>
          <input type="password" value={key} onChange={e => setKey(e.target.value)} placeholder="sk-…" />
        </div>

        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: '0.8em', color: 'var(--text-muted)', marginBottom: 4 }}>Base URL</div>
          <input value={url} onChange={e => setUrl(e.target.value)} placeholder="https://api.deepseek.com" />
        </div>

        <div style={{ display: 'grid', gap: 10, marginBottom: 20 }}>
          <ModelSelect label="对话模型" value={dialogueModel} onChange={setDialogueModel} />
          <ModelSelect label="报告模型" value={reportModel} onChange={setReportModel} />
          <ModelSelect label="实验 Agent 模型" value={experimentModel} onChange={setExperimentModel} />
        </div>

        <div style={{ display: 'flex', gap: 8 }}>
          <button className="primary" onClick={() => onSave({ apiKey: key, baseUrl: url, dialogueModel, reportModel, experimentModel })} disabled={!key.trim()} style={{ flex: 1 }}>
            保存并开始
          </button>
          {config.apiKey && (
            <button onClick={onClose} style={{ flex: 1 }}>
              取消
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function ModelSelect({ label, value, onChange }: { label: string; value: ModelId; onChange: (value: ModelId) => void }) {
  return (
    <label style={{ fontSize: '0.8em', color: 'var(--text-muted)' }}>
      <span style={{ display: 'block', marginBottom: 4 }}>{label}</span>
      <select value={value} onChange={e => onChange(e.target.value as ModelId)}>
        {MODEL_OPTIONS.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select>
    </label>
  );
}
