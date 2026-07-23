import { useRef, useState, type FormEvent } from 'react';
import { KeyRound, Save, X } from 'lucide-react';
import { MODEL_OPTIONS } from '../hooks/useConfig';
import type { AppConfig, ModelId } from '../hooks/useConfig';
import { useDialogFocus } from '../hooks/useDialogFocus';
import { AppButton } from './ui/AppButton';

interface Props {
  config: AppConfig;
  onSave: (config: AppConfig) => void;
  onClose: () => void;
}

export function ConfigModal({ config, onSave, onClose }: Props) {
  const [key, setKey] = useState(config.apiKey);
  const [url, setUrl] = useState(config.baseUrl);
  const [dialogueModel, setDialogueModel] = useState<ModelId>(config.dialogueModel);
  const [reportModel, setReportModel] = useState<ModelId>(config.reportModel);
  const [experimentModel, setExperimentModel] = useState<ModelId>(config.experimentModel);
  const keyRef = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  useDialogFocus(keyRef, { dialogRef, onClose });

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const apiKey = key.trim();
    if (!apiKey) return;
    onSave({ apiKey, baseUrl: url.trim(), dialogueModel, reportModel, experimentModel });
  };

  return (
    <div ref={dialogRef} className="modal-overlay" role="dialog" aria-modal="true" aria-labelledby="config-modal-title">
      <div className="modal config-modal">
        <header className="config-modal-heading">
          <span className="config-modal-icon"><KeyRound size={18} strokeWidth={1.8} aria-hidden="true" /></span>
          <div>
            <h2 id="config-modal-title">配置 API Key</h2>
            <p>修改模型连接设置。</p>
          </div>
        </header>
        <p className="config-modal-note">API Key 保存在当前浏览器；在线请求会发送给当前 AutoAD 服务，不写入实验产物、报告或任务记录。</p>

        <form className="config-form" onSubmit={handleSubmit}>
          <label className="config-field" htmlFor="config-api-key">
            <span>API Key</span>
            <input id="config-api-key" ref={keyRef} type="password" value={key} onChange={event => setKey(event.target.value)} placeholder="输入 API Key" autoComplete="off" required />
          </label>
          <label className="config-field" htmlFor="config-base-url">
            <span>Base URL</span>
            <input id="config-base-url" value={url} onChange={event => setUrl(event.target.value)} placeholder="https://api.deepseek.com" inputMode="url" autoComplete="url" required />
          </label>
          <ModelSelect id="config-dialogue-model" label="研究对话模型" value={dialogueModel} onChange={setDialogueModel} />
          <ModelSelect id="config-report-model" label="实验报告模型" value={reportModel} onChange={setReportModel} />
          <ModelSelect id="config-experiment-model" label="实验 Agent 模型" value={experimentModel} onChange={setExperimentModel} />

          <div className="config-actions">
            <AppButton variant="primary" type="submit" disabled={!key.trim()}>
              <Save size={16} strokeWidth={1.8} aria-hidden="true" />
              保存并开始
            </AppButton>
            {config.apiKey && (
              <AppButton type="button" onClick={onClose}>
                <X size={16} strokeWidth={1.8} aria-hidden="true" />
                取消
              </AppButton>
            )}
          </div>
        </form>
      </div>
    </div>
  );
}

function ModelSelect({ id, label, value, onChange }: { id: string; label: string; value: ModelId; onChange: (value: ModelId) => void }) {
  return (
    <label className="config-field" htmlFor={id}>
      <span>{label}</span>
      <select id={id} value={value} onChange={event => onChange(event.target.value as ModelId)}>
        {MODEL_OPTIONS.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select>
    </label>
  );
}
