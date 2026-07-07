import { useState } from 'react';
import type { ExperimentConfig } from '../lib/types';

interface Props {
  experiment: ExperimentConfig;
  onSave: (exp: ExperimentConfig) => void;
  defaultApiKey: string;
}

export function SettingsTab({ experiment, onSave, defaultApiKey }: Props) {
  const [exp, setExp] = useState<ExperimentConfig>({
    ...experiment,
    apiKey: experiment.apiKey || defaultApiKey,
  });
  const [openLLM, setOpenLLM] = useState(true);
  const [openBudget, setOpenBudget] = useState(true);
  const [openSearch, setOpenSearch] = useState(false);
  const [saved, setSaved] = useState(false);

  const set = (k: keyof ExperimentConfig, v: any) => {
    setExp(p => ({ ...p, [k]: v }));
    setSaved(false);
  };

  const save = () => {
    onSave(exp);
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
  };

  const s: Record<string, React.CSSProperties> = {
    section: {
      marginBottom: 8, border: '1px solid var(--border)', borderRadius: 6,
      overflow: 'hidden',
    },
    header: {
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      padding: '6px 10px', background: 'var(--bg)', fontSize: '0.8em',
      color: 'var(--text-muted)', cursor: 'pointer', border: 'none', width: '100%', textAlign: 'left',
    },
    body: { padding: '6px 10px', display: 'flex', flexDirection: 'column', gap: 6 },
    label: { fontSize: '0.72em', color: 'var(--text-dim)', marginBottom: 1 },
    input: {
      width: '100%', padding: '4px 8px', borderRadius: 4, border: '1px solid var(--border)',
      background: 'var(--bg)', color: 'var(--text)', fontSize: '0.78em',
    },
    select: {
      width: '100%', padding: '4px 8px', borderRadius: 4, border: '1px solid var(--border)',
      background: 'var(--bg)', color: 'var(--text)', fontSize: '0.78em',
    },
    toggle: {
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      fontSize: '0.78em', color: 'var(--text)',
    },
    range: {
      width: '100%', accentColor: 'var(--blue)',
    },
    row: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: '0.72em' },
    saveBtn: {
      width: '100%', padding: '6px 0', borderRadius: 4, border: 'none',
      background: 'var(--blue)', color: '#fff', fontSize: '0.82em', cursor: 'pointer', marginTop: 4,
    },
    savedBtn: {
      width: '100%', padding: '6px 0', borderRadius: 4, border: 'none',
      background: 'var(--green)', color: '#fff', fontSize: '0.82em', cursor: 'pointer', marginTop: 4,
    },
    note: { fontSize: '0.68em', color: 'var(--text-dim)', lineHeight: 1.4, padding: '4px 0' },
  };

  const toggleBtnStyle = (on: boolean): React.CSSProperties => ({
    width: 34, height: 20, borderRadius: 10, border: 'none', cursor: 'pointer',
    background: on ? 'var(--green)' : 'var(--border)', position: 'relative',
    transition: 'background 0.2s',
  });

  const toggleDotStyle = (on: boolean): React.CSSProperties => ({
    width: 14, height: 14, borderRadius: 7, background: 'var(--bg)',
    position: 'absolute', top: 3,
    left: on ? 17 : 3, transition: 'left 0.2s',
  });

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      {/* LLM Configuration */}
      <div style={s.section}>
        <button style={s.header} onClick={() => setOpenLLM(!openLLM)}>
          <span>{openLLM ? '▼' : '▶'} LLM Configuration</span>
        </button>
        {openLLM && (
          <div style={s.body}>
            <div style={s.label}>Provider</div>
            <select style={s.select} value={exp.provider} onChange={e => set('provider', e.target.value)}>
              <option value="openai-chat">openai-chat (DeepSeek)</option>
              <option value="anthropic">anthropic (Claude)</option>
              <option value="openai-responses">openai-responses (GPT)</option>
              <option value="auto">auto</option>
            </select>
            <div style={s.label}>Model</div>
            <input style={s.input} value={exp.model} onChange={e => set('model', e.target.value)} placeholder="deepseek-v4-flash" />
            <div style={s.label}>API Key</div>
            <input style={s.input} type="password" value={exp.apiKey} onChange={e => set('apiKey', e.target.value)} placeholder="sk-..." />
            <div style={s.label}>Base URL</div>
            <input style={s.input} value={exp.baseUrl} onChange={e => set('baseUrl', e.target.value)} placeholder="https://api.deepseek.com" />
            <div style={s.label}>Reasoning Effort</div>
            <select style={s.select} value={exp.reasoningEffort} onChange={e => set('reasoningEffort', e.target.value)}>
              <option value="high">high</option>
              <option value="medium">medium</option>
              <option value="low">low</option>
              <option value="none">none</option>
            </select>
          </div>
        )}
      </div>

      {/* Experiment Budget */}
      <div style={s.section}>
        <button style={s.header} onClick={() => setOpenBudget(!openBudget)}>
          <span>{openBudget ? '▼' : '▶'} Experiment Budget</span>
        </button>
        {openBudget && (
          <div style={s.body}>
            <div style={s.row}>
              <span style={s.label}>Max Cycles</span>
              <span style={{ color: 'var(--blue)', fontSize: '0.78em' }}>{exp.maxCycles}</span>
            </div>
            <input
              type="range" min={2} max={100} value={exp.maxCycles}
              onChange={e => set('maxCycles', Number(e.target.value))}
              style={s.range}
            />
            <div style={s.row}>
              <span style={s.label}>Max Turns/Executor</span>
              <span style={{ color: 'var(--blue)', fontSize: '0.78em' }}>{exp.maxTurns}</span>
            </div>
            <input
              type="range" min={10} max={200} value={exp.maxTurns}
              onChange={e => set('maxTurns', Number(e.target.value))}
              style={s.range}
            />
            <div style={s.label}>Executor Timeout</div>
            <select style={s.select} value={exp.executorTimeout.toString()} onChange={e => set('executorTimeout', Number(e.target.value))}>
              <option value="3600">1 hour</option>
              <option value="21600">6 hours</option>
              <option value="86400">24 hours</option>
              <option value="172800">48 hours</option>
              <option value="604800">7 days</option>
            </select>
          </div>
        )}
      </div>

      {/* Search */}
      <div style={s.section}>
        <button style={s.header} onClick={() => setOpenSearch(!openSearch)}>
          <span>{openSearch ? '▼' : '▶'} Search (alphaXiv)</span>
        </button>
        {openSearch && (
          <div style={s.body}>
            <div style={s.toggle}>
              <span>Enable Search</span>
              <button style={toggleBtnStyle(exp.searchEnabled)} onClick={() => set('searchEnabled', !exp.searchEnabled)}>
                <div style={toggleDotStyle(exp.searchEnabled)} />
              </button>
            </div>
            <div style={s.toggle}>
              <span>Auto-check Ideas</span>
              <button style={toggleBtnStyle(exp.autoSearch)} onClick={() => set('autoSearch', !exp.autoSearch)}>
                <div style={toggleDotStyle(exp.autoSearch)} />
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Info */}
      <div style={{ ...s.section, borderColor: '#1a3a3a' }}>
        <div style={{ padding: '6px 10px' }}>
          <div style={s.note}>
            Experiment agents run in <strong>auto</strong> mode — no human-in-the-loop during experiments.
          </div>
          <div style={s.note}>
            Each experiment runs in an isolated git worktree. Only held-out test gains above the merge threshold are accepted.
          </div>
        </div>
      </div>

      <button style={saved ? s.savedBtn : s.saveBtn} onClick={save}>
        {saved ? 'Saved' : 'Save Settings'}
      </button>
    </div>
  );
}
