import { useState, useCallback } from 'react';
import { ChatInput } from './components/ChatInput';
import { PlusMenu } from './components/PlusMenu';
import { UserMessage, AssistantMessage, WelcomeMessage } from './components/Messages';
import { ToastContainer } from './components/Toast';
import { ConfigModal } from './components/ConfigModal';
import { FirstRunSetup } from './components/FirstRunSetup';
import { StatusBar } from './components/StatusBar';
import { Sidebar } from './components/Sidebar';
import { DevMockPanel } from './components/DevMockPanel';
import { useConfig } from './hooks/useConfig';
import { useAutoScroll } from './hooks/useAutoScroll';
import { useWebSocket } from './hooks/useWebSocket';
import { sendChat, createRun, getSources, getJobs, getArtifact } from './lib/api';
import { generateId } from './lib/mock';
import type { Message, ToastItem, SourceItem, JobItem, WSMessage } from './lib/types';

interface ArtifactEntry {
  path: string;
  label: string;
  content?: string;
}

export default function App() {
  const { config, saveConfig, showConfig, openConfig, closeConfig } = useConfig();
  const [runId, setRunId] = useState<string>('');
  const [taskStatus, setTaskStatus] = useState<string>('Ready');
  const [messages, setMessages] = useState<Message[]>([]);
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const [sources, setSources] = useState<SourceItem[]>([]);
  const [jobs, setJobs] = useState<JobItem[]>([]);
  const [artifacts, setArtifacts] = useState<ArtifactEntry[]>([]);
  const [showDev, setShowDev] = useState(false);
  const bottomRef = useAutoScroll([messages]);

  const addToast = useCallback((message: string, kind: 'success' | 'error' | 'info') => {
    setToasts(prev => [...prev, { id: generateId(), message, kind }]);
  }, []);

  const removeToast = useCallback((id: string) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  const refreshSidebar = useCallback(async () => {
    if (!runId) return;
    const s = await getSources(runId).catch(() => []);
    const j = await getJobs(runId).catch(() => []);
    setSources(s.map((src: any) => ({ sourceId: src.source_id || generateId(), kind: src.kind || 'unknown', label: src.user_label || src.source_id || 'source', status: src.status || 'unknown' })));
    setJobs(j.map((job: any) => ({ jobId: job.job_id || generateId(), jobType: job.job_type || 'unknown', status: job.status || 'unknown', sourceLabel: job.outputs?.[0] || '' })));
  }, [runId]);

  // ── First-run: create run on save ──
  const handleFirstRunSave = useCallback(async (c: typeof config) => {
    saveConfig(c);
    try {
      const r = await createRun();
      setRunId(r.run_id);
      setTaskStatus('Ready');
    } catch {
      setRunId('run_default');
    }
  }, [saveConfig]);

  // ── Real chat handler ──
  const handleSend = useCallback(async (text: string) => {
    const userMsg: Message = { id: generateId(), role: 'user', content: text, timestamp: Date.now() };
    setMessages(prev => [...prev, userMsg]);

    try {
      const res = await sendChat(text, runId || 'run_default');
      setMessages(prev => [...prev, {
        id: generateId(), role: 'assistant', content: res.reply, timestamp: Date.now(),
      }]);
      setTaskStatus('Working');
      await refreshSidebar();
    } catch {
      setMessages(prev => [...prev, {
        id: generateId(), role: 'assistant',
        content: '抱歉，后端未启动。请运行: uv run uvicorn autoad_researcher.server.main:app --port 8000',
        timestamp: Date.now(),
      }]);
    }
  }, [runId, refreshSidebar]);

  // ── File upload — goes through real backend ──
  const handleFile = useCallback((name: string) => {
    setMessages(prev => [...prev, { id: generateId(), role: 'user', content: '📎 ' + name, timestamp: Date.now() }]);
    const reply = '文件上传接口尚未接入后端。请通过聊天框粘贴 arXiv/GitHub 链接触发 PipelineJob。';
    setMessages(prev => [...prev, { id: generateId(), role: 'assistant', content: reply, timestamp: Date.now() }]);
  }, []);

  // ── WebSocket: real-time event handling ──
  const onWsMessage = useCallback((msg: WSMessage) => {
    if (msg.type === 'source.created') {
      setSources(prev => [...prev, { sourceId: msg.sourceId || generateId(), kind: msg.kind || 'unknown', label: msg.sourceId || 'source', status: 'registered' }]);
    }
    if (msg.type === 'job.queued') {
      setJobs(prev => [...prev, { jobId: msg.jobId || generateId(), jobType: msg.jobType || 'unknown', status: 'queued' }]);
    }
    if (msg.type === 'job.started') {
      setJobs(prev => prev.map(j => j.jobId === msg.jobId ? { ...j, status: 'running' } : j));
    }
    if (msg.type === 'job.completed') {
      setJobs(prev => prev.map(j => j.jobId === msg.jobId ? { ...j, status: 'completed' } : j));
      setTaskStatus('Ready');
    }
    if (msg.type === 'job.failed') {
      setJobs(prev => prev.map(j => j.jobId === msg.jobId ? { ...j, status: 'failed' } : j));
      setTaskStatus('Error');
    }
    if (msg.type === 'artifact.created') {
      const paths: string[] = (msg as any).paths || [];
      for (const p of paths) {
        const isMd = p.endsWith('.md');
        const label = isMd
          ? (p.includes('paper') ? '📄 论文摘要' : p.includes('repo') ? '📦 仓库摘要' : p.includes('source') ? '🌐 网页摘要' : `📝 ${p.split('/').pop()}`)
          : p;
        setArtifacts(prev => {
          if (prev.some(a => a.path === p)) return prev;
          return [...prev, { path: p, label }];
        });
      }
    }
    if (msg.type === 'toast.success' && msg.message) addToast(msg.message, 'success');
    if (msg.type === 'toast.error' && msg.message) addToast(msg.message, 'error');
  }, [addToast]);

  useWebSocket({ runId, onMessage: onWsMessage, enabled: !!runId });

  // ── FirstRunSetup when no API key ──
  if (!config.apiKey) {
    return <FirstRunSetup onSave={handleFirstRunSave} />;
  }

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
      {showConfig && <ConfigModal config={config} onSave={saveConfig} onClose={closeConfig} />}
      <ToastContainer toasts={toasts} onRemove={removeToast} />

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 16px', borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 12 }}>
          <span style={{ fontWeight: 600, fontSize: '1.05em', color: 'var(--blue)' }}>AutoAD Researcher</span>
          <span style={{ fontSize: '0.85em', color: 'var(--text-muted)' }}>当前任务：新的研究任务</span>
          <span style={{
            fontSize: '0.75em', padding: '2px 8px', borderRadius: 4,
            background: taskStatus === 'Ready' ? '#1a3a1a' : taskStatus === 'Working' ? '#3a2a0a' : taskStatus === 'Error' ? '#3a1a1a' : '#1a1a3a',
            color: taskStatus === 'Ready' ? 'var(--green)' : taskStatus === 'Working' ? 'var(--orange)' : taskStatus === 'Error' ? 'var(--red)' : 'var(--blue)',
          }}>
            {taskStatus}
          </span>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <button onClick={openConfig} title="配置" style={{ padding: '6px 10px' }}>⚙</button>
        </div>
      </div>

      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
        {/* Chat area */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          <div style={{ flex: 1, overflowY: 'auto', padding: '12px 20px' }}>
            {messages.length === 0 && <WelcomeMessage />}
            {messages.map(msg =>
              msg.role === 'user' ? <UserMessage key={msg.id} msg={msg} /> : <AssistantMessage key={msg.id} msg={msg} />
            )}
            {messages.length > 0 && <div ref={bottomRef} />}
          </div>
          <div style={{ padding: '0 16px', flexShrink: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <div style={{ flex: 1 }}><ChatInput onSend={handleSend} /></div>
              <PlusMenu onFile={handleFile} />
            </div>
            <div className="kbd-hint">Enter 发送 · Shift+Enter 换行 · 粘贴 arXiv/GitHub 链接到输入框</div>
            <StatusBar sources={sources} jobs={jobs} evidenceCount={artifacts.length} draftReady={false} />
          </div>
        </div>

        {/* Right sidebar — Evidence / Artifacts */}
        <Sidebar sources={sources} jobs={jobs} evidenceCount={artifacts.length} draftReady={false}>
          {artifacts.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <div style={{ fontSize: '0.8em', color: 'var(--text-muted)', marginBottom: 6 }}>Markdown 摘要</div>
              {artifacts.map(a => (
                <ArtifactItem key={a.path} artifact={a} runId={runId} />
              ))}
            </div>
          )}

          {/* Developer Details */}
          <div style={{ marginTop: 'auto', paddingTop: 12, borderTop: '1px solid var(--border)' }}>
            <button onClick={() => setShowDev(!showDev)} style={{ width: '100%', fontSize: '0.78em', padding: '4px 0', background: 'transparent', border: 'none', color: 'var(--text-dim)' }}>
              {showDev ? '▼' : '▶'} Developer Details
            </button>
            {showDev && (
              <div style={{ fontSize: '0.72em', color: 'var(--text-dim)', marginTop: 4 }}>
                <div>run_id: {runId || '未创建'}</div>
                <div>sources: {sources.length} | jobs: {jobs.length}</div>
                {artifacts.map(a => <div key={a.path}>artifact: {a.path}</div>)}
                {import.meta.env.DEV && (
                  <DevMockPanel addToast={addToast} setMessages={setMessages} />
                )}
              </div>
            )}
          </div>
        </Sidebar>
      </div>
    </div>
  );
}

function ArtifactItem({ artifact, runId }: { artifact: ArtifactEntry; runId: string }) {
  const [content, setContent] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  const load = async () => {
    if (content) { setOpen(!open); return; }
    try {
      const res = await getArtifact(runId, artifact.path);
      setContent(res.content);
      setOpen(true);
    } catch {
      setContent('无法加载');
    }
  };

  return (
    <div style={{ marginBottom: 4 }}>
      <button onClick={load} style={{
        width: '100%', textAlign: 'left', padding: '4px 8px', fontSize: '0.82em',
        background: 'transparent', border: 'none', color: 'var(--blue)', cursor: 'pointer',
      }}>
        {open ? '▼' : '▶'} {artifact.label}
      </button>
      {open && content && (
        <div style={{
          maxHeight: 300, overflow: 'auto', padding: '6px 8px', fontSize: '0.78em',
          color: 'var(--text)', background: 'var(--bg)', borderRadius: 4, margin: '4px 0',
          whiteSpace: 'pre-wrap', border: '1px solid var(--border)',
        }}>
          {content.slice(0, 3000)}
        </div>
      )}
    </div>
  );
}
