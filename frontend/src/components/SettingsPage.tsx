import { useState } from 'react';
import type { ExperimentConfig } from '../lib/types';

interface Props {
  experiment: ExperimentConfig;
  defaultApiKey: string;
  onSave: (exp: ExperimentConfig) => void;
  onBack: () => void;
}

export function SettingsPage({ experiment, defaultApiKey, onSave, onBack }: Props) {
  const [exp, setExp] = useState<ExperimentConfig>({
    ...experiment,
    apiKey: experiment.apiKey || defaultApiKey,
  });
  const [saved, setSaved] = useState(false);

  const set = (k: keyof ExperimentConfig, v: any) => {
    setExp(p => ({ ...p, [k]: v }));
    setSaved(false);
  };

  const save = () => {
    onSave(exp);
    setSaved(true);
    setTimeout(() => setSaved(false), 1800);
  };

  const s = {
    card: {
      border: '1px solid var(--border)', borderRadius: 8, padding: 24,
      background: 'var(--bg)', marginBottom: 16,
    } as React.CSSProperties,
    title: {
      fontSize: '0.9em', fontWeight: 600, color: 'var(--blue)',
      marginBottom: 16, paddingBottom: 8, borderBottom: '1px solid var(--border)',
    } as React.CSSProperties,
    label: { fontSize: '0.78em', color: 'var(--text-muted)', marginBottom: 4 } as React.CSSProperties,
    input: {
      width: '100%', padding: '8px 12px', borderRadius: 6,
      border: '1px solid var(--border)', background: 'var(--bg-panel)',
      color: 'var(--text)', fontSize: '0.9em', marginBottom: 14,
    } as React.CSSProperties,
    select: {
      width: '100%', padding: '8px 12px', borderRadius: 6,
      border: '1px solid var(--border)', background: 'var(--bg-panel)',
      color: 'var(--text)', fontSize: '0.9em', marginBottom: 14,
    } as React.CSSProperties,
    row: {
      display: 'flex', alignItems: 'center', gap: 8,
      marginBottom: 14, color: 'var(--text)',
    } as React.CSSProperties,
    toggle: (on: boolean): React.CSSProperties => ({
      width: 44, height: 24, borderRadius: 12, border: 'none', cursor: 'pointer',
      background: on ? 'var(--green)' : 'var(--border)', position: 'relative',
      flexShrink: 0, transition: 'background 0.2s',
    }),
    dot: (on: boolean): React.CSSProperties => ({
      width: 18, height: 18, borderRadius: 9, background: '#fff',
      position: 'absolute', top: 3, left: on ? 23 : 3, transition: 'left 0.2s',
    }),
    range: { width: '100%', accentColor: 'var(--blue)', marginBottom: 8 },
    hint: { fontSize: '0.72em', color: 'var(--text-dim)', marginTop: 4, lineHeight: 1.5 },
  };

  return (
    <div style={{
      flex: 1, height: '100%', overflow: 'auto',
      display: 'flex', justifyContent: 'center',
    }}>
      <div style={{ width: 640, maxWidth: '90%', padding: '32px 0' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
          <div>
            <div style={{ fontSize: '1.3em', fontWeight: 600, color: 'var(--text)' }}>
              Arbor Experiment Settings
            </div>
            <div style={{ fontSize: '0.82em', color: 'var(--text-muted)', marginTop: 4 }}>
              Configure how experiment agents explore and evaluate hypotheses
            </div>
          </div>
          <button
            onClick={onBack}
            style={{
              padding: '6px 14px', border: '1px solid var(--border)', borderRadius: 6,
              background: 'transparent', color: 'var(--text-muted)', cursor: 'pointer',
              fontSize: '0.85em',
            }}
          >
            Back to Chat
          </button>
        </div>

        {/* LLM Config */}
        <div style={s.card}>
          <div style={s.title}>LLM Configuration</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 24px' }}>
            <div>
              <div style={s.label}>Provider</div>
              <select style={s.select} value={exp.provider} onChange={e => set('provider', e.target.value)}>
                <option value="openai-chat">openai-chat</option>
                <option value="anthropic">anthropic</option>
                <option value="openai-responses">openai-responses</option>
                <option value="auto">auto</option>
              </select>
            </div>
            <div>
              <div style={s.label}>Model</div>
              <input style={s.input} value={exp.model} onChange={e => set('model', e.target.value)} placeholder="deepseek-v4-flash" />
            </div>
            <div>
              <div style={s.label}>API Key</div>
              <input style={s.input} type="password" value={exp.apiKey} onChange={e => set('apiKey', e.target.value)} placeholder="sk-..." />
            </div>
            <div>
              <div style={s.label}>Base URL</div>
              <input style={s.input} value={exp.baseUrl} onChange={e => set('baseUrl', e.target.value)} placeholder="https://api.deepseek.com" />
            </div>
            <div>
              <div style={s.label}>Reasoning Effort</div>
              <select style={s.select} value={exp.reasoningEffort} onChange={e => set('reasoningEffort', e.target.value)}>
                <option value="high">high</option>
                <option value="medium">medium</option>
                <option value="low">low</option>
                <option value="none">none</option>
              </select>
            </div>
            <div />
          </div>
        </div>

        {/* Budget */}
        <div style={s.card}>
          <div style={s.title}>Experiment Budget</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 24px' }}>
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                <span style={s.label}>Max Cycles</span>
                <span style={{ fontSize: '0.85em', color: 'var(--blue)', fontWeight: 600 }}>{exp.maxCycles}</span>
              </div>
              <input type="range" min={2} max={100} value={exp.maxCycles} onChange={e => set('maxCycles', Number(e.target.value))} style={s.range} />
              <div style={s.hint}>Number of idea experiments to explore</div>
            </div>
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                <span style={s.label}>Max Turns / Executor</span>
                <span style={{ fontSize: '0.85em', color: 'var(--blue)', fontWeight: 600 }}>{exp.maxTurns}</span>
              </div>
              <input type="range" min={10} max={200} value={exp.maxTurns} onChange={e => set('maxTurns', Number(e.target.value))} style={s.range} />
              <div style={s.hint}>Tool-calling turns per experiment</div>
            </div>
            <div>
              <div style={s.label}>Executor Timeout</div>
              <select style={s.select} value={exp.executorTimeout.toString()} onChange={e => set('executorTimeout', Number(e.target.value))}>
                <option value="3600">1 hour</option>
                <option value="21600">6 hours</option>
                <option value="86400">24 hours</option>
                <option value="172800">48 hours</option>
                <option value="604800">7 days</option>
              </select>
            </div>
            <div />
          </div>
        </div>

        {/* Search */}
        <div style={s.card}>
          <div style={s.title}>Literature Search (alphaXiv)</div>
          <div style={s.row}>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: '0.85em' }}>Enable Search</div>
              <div style={s.hint}>Query alphaXiv API before dispatching experiments</div>
            </div>
            <button style={s.toggle(exp.searchEnabled)} onClick={() => set('searchEnabled', !exp.searchEnabled)}>
              <div style={s.dot(exp.searchEnabled)} />
            </button>
          </div>
          <div style={{ ...s.row, marginBottom: 0 }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: '0.85em' }}>Auto-check Idea Novelty</div>
              <div style={s.hint}>Check each new idea against prior art before running it</div>
            </div>
            <button style={s.toggle(exp.autoSearch)} onClick={() => set('autoSearch', !exp.autoSearch)}>
              <div style={s.dot(exp.autoSearch)} />
            </button>
          </div>
        </div>

        {/* Actions */}
        <div style={{ display: 'flex', gap: 12, justifyContent: 'flex-end' }}>
          <button
            onClick={onBack}
            style={{
              padding: '10px 24px', border: '1px solid var(--border)', borderRadius: 8,
              background: 'transparent', color: 'var(--text-muted)', cursor: 'pointer',
              fontSize: '0.9em',
            }}
          >
            Cancel
          </button>
          <button
            onClick={save}
            disabled={!exp.apiKey.trim()}
            style={{
              padding: '10px 32px', border: 'none', borderRadius: 8,
              background: saved ? 'var(--green)' : exp.apiKey.trim() ? 'var(--blue)' : 'var(--border)',
              color: '#fff', cursor: exp.apiKey.trim() ? 'pointer' : 'default',
              fontSize: '0.9em', fontWeight: 500,
              transition: 'background 0.3s',
            }}
          >
            {saved ? 'Saved' : 'Save Settings'}
          </button>
        </div>
      </div>
    </div>
  );
}
