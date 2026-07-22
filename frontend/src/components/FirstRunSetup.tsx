import { useState, type FormEvent } from 'react';
import { ArrowRight, KeyRound, ShieldCheck } from 'lucide-react';
import type { AppConfig } from '../hooks/useConfig';
import { AppButton } from './ui/AppButton';
import { Surface } from './ui/Surface';
import { ThemeToggle } from '../theme/ThemeToggle';

interface Props {
  onSave: (config: AppConfig) => Promise<void>;
}

export function FirstRunSetup({ onSave }: Props) {
  const [key, setKey] = useState('');
  const [url, setUrl] = useState('https://api.deepseek.com');
  const [model, setModel] = useState('deepseek-v4-flash');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const apiKey = key.trim();
    if (!apiKey || saving) return;
    setError(null);
    setSaving(true);
    try {
      await onSave({ apiKey, baseUrl: url.trim(), model: model.trim() });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '配置保存失败，请重试。');
    } finally {
      setSaving(false);
    }
  };

  return (
    <main className="first-run-shell">
      <div className="first-run-stage">
        <header className="first-run-header">
          <div className="first-run-brand">
            <span className="first-run-brand-icon"><KeyRound size={17} strokeWidth={1.8} aria-hidden="true" /></span>
            <span>AutoAD Researcher</span>
          </div>
          <ThemeToggle />
        </header>

        <Surface className="first-run-surface">
          <div className="first-run-heading">
            <span className="first-run-heading-icon"><KeyRound size={20} strokeWidth={1.8} aria-hidden="true" /></span>
            <div>
              <h1>连接研究工作台</h1>
              <p>配置模型连接后开始你的第一个研究任务。</p>
            </div>
          </div>

          <form className="first-run-form" onSubmit={handleSubmit} aria-busy={saving}>
            <label className="config-field">
              <span>API Key</span>
              <input
                type="password"
                value={key}
                onChange={event => setKey(event.target.value)}
                placeholder="输入 API Key"
                autoComplete="off"
                autoFocus
                required
              />
            </label>
            <label className="config-field">
              <span>Base URL</span>
              <input
                value={url}
                onChange={event => setUrl(event.target.value)}
                placeholder="https://api.deepseek.com"
                inputMode="url"
                autoComplete="url"
                required
              />
            </label>
            <label className="config-field">
              <span>Model</span>
              <input
                value={model}
                onChange={event => setModel(event.target.value)}
                placeholder="deepseek-v4-flash"
                autoComplete="off"
                required
              />
            </label>

            <div className="first-run-privacy">
              <ShieldCheck size={16} strokeWidth={1.8} aria-hidden="true" />
              <span>API Key 只保存在本设备浏览器中。</span>
            </div>
            {error && <div className="config-error" role="alert">{error}</div>}

            <AppButton variant="primary" type="submit" disabled={!key.trim() || saving} className="first-run-submit">
              <ArrowRight size={16} strokeWidth={1.9} aria-hidden="true" />
              {saving ? '正在创建任务…' : '保存并创建任务'}
            </AppButton>
          </form>
        </Surface>
      </div>
    </main>
  );
}
