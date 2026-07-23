import { useState, type FormEvent } from 'react';
import { ArrowRight, KeyRound, ShieldCheck } from 'lucide-react';
import { MODEL_OPTIONS } from '../hooks/useConfig';
import type { AppConfig, ModelId } from '../hooks/useConfig';
import { AppButton } from './ui/AppButton';
import { Surface } from './ui/Surface';
import { ThemeToggle } from '../theme/ThemeToggle';

interface Props {
  onSave: (config: AppConfig) => Promise<void>;
}

export function FirstRunSetup({ onSave }: Props) {
  const [key, setKey] = useState('');
  const [url, setUrl] = useState('https://api.deepseek.com');
  const [dialogueModel, setDialogueModel] = useState<ModelId>('deepseek-v4-flash');
  const [reportModel, setReportModel] = useState<ModelId>('deepseek-v4-flash');
  const [experimentModel, setExperimentModel] = useState<ModelId>('deepseek-v4-pro');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const apiKey = key.trim();
    if (!apiKey || saving) return;
    setError(null);
    setSaving(true);
    try {
      await onSave({ apiKey, baseUrl: url.trim(), dialogueModel, reportModel, experimentModel });
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
            <label className="config-field" htmlFor="first-run-api-key">
              <span>API Key</span>
              <input
                id="first-run-api-key"
                type="password"
                value={key}
                onChange={event => setKey(event.target.value)}
                placeholder="输入 API Key"
                autoComplete="off"
                autoFocus
                required
              />
            </label>
            <label className="config-field" htmlFor="first-run-base-url">
              <span>Base URL</span>
              <input
                id="first-run-base-url"
                value={url}
                onChange={event => setUrl(event.target.value)}
                placeholder="https://api.deepseek.com"
                inputMode="url"
                autoComplete="url"
                required
              />
            </label>
            <ModelSelect id="first-run-dialogue-model" label="研究对话模型" value={dialogueModel} onChange={setDialogueModel} />
            <ModelSelect id="first-run-report-model" label="实验报告模型" value={reportModel} onChange={setReportModel} />
            <ModelSelect id="first-run-experiment-model" label="实验 Agent 模型" value={experimentModel} onChange={setExperimentModel} />

            <div className="first-run-privacy">
              <ShieldCheck size={16} strokeWidth={1.8} aria-hidden="true" />
              <span>API Key 保存在当前浏览器；在线请求会发送给当前 AutoAD 服务。</span>
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
