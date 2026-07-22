import { useRef, useState, type FormEvent } from 'react';
import { KeyRound, Save, X } from 'lucide-react';
import type { AppConfig } from '../hooks/useConfig';
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
  const [model, setModel] = useState(config.model);
  const keyRef = useRef<HTMLInputElement>(null);
  useDialogFocus(keyRef);

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const apiKey = key.trim();
    if (!apiKey) return;
    onSave({ apiKey, baseUrl: url.trim(), model: model.trim() });
  };

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-labelledby="config-modal-title">
      <div className="modal config-modal">
        <header className="config-modal-heading">
          <span className="config-modal-icon"><KeyRound size={18} strokeWidth={1.8} aria-hidden="true" /></span>
          <div>
            <h2 id="config-modal-title">配置 API Key</h2>
            <p>修改模型连接设置。</p>
          </div>
        </header>
        <p className="config-modal-note">API Key 只保存在本设备浏览器中，不上传服务器。</p>

        <form className="config-form" onSubmit={handleSubmit}>
          <label className="config-field">
            <span>API Key</span>
            <input ref={keyRef} type="password" value={key} onChange={event => setKey(event.target.value)} placeholder="输入 API Key" autoComplete="off" required />
          </label>
          <label className="config-field">
            <span>Base URL</span>
            <input value={url} onChange={event => setUrl(event.target.value)} placeholder="https://api.deepseek.com" inputMode="url" autoComplete="url" required />
          </label>
          <label className="config-field">
            <span>Model</span>
            <input value={model} onChange={event => setModel(event.target.value)} placeholder="deepseek-v4-flash" autoComplete="off" required />
          </label>

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
