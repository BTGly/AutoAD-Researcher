import { useState, useCallback } from 'react';
import { ChatInput } from './components/ChatInput';
import { PlusMenu } from './components/PlusMenu';
import { UserMessage, AssistantMessage, WelcomeMessage } from './components/Messages';
import { ToastContainer } from './components/Toast';
import { ConfigModal } from './components/ConfigModal';
import { StatusBar } from './components/StatusBar';
import { Sidebar } from './components/Sidebar';
import { DemoPanel } from './components/DemoPanel';
import { useConfig } from './hooks/useConfig';
import { useAutoScroll } from './hooks/useAutoScroll';
import { sendChat, createRun, getSources, getJobs } from './lib/api';
import { generateId, mockParseFlow, mockUrlFlow, mockSearchFlow } from './lib/mock';
import type { Message, ToastItem, ToolLine, SourceItem, JobItem, WSMessage } from './lib/types';

function useRealChat() {
  const [runId, setRunId] = useState<string>('run_default');

  const initRun = useCallback(async () => {
    try {
      const r = await createRun();
      setRunId(r.run_id);
      return r.run_id;
    } catch {
      return runId;
    }
  }, [runId]);

  return { runId, setRunId, initRun };
}

export default function App() {
  const { config, saveConfig, showConfig, openConfig, closeConfig } = useConfig();
  const { runId, initRun } = useRealChat();
  const [messages, setMessages] = useState<Message[]>([]);
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const [sources, setSources] = useState<SourceItem[]>([]);
  const [jobs, setJobs] = useState<JobItem[]>([]);
  const [processing, setProcessing] = useState(false);
  const bottomRef = useAutoScroll([messages]);

  const addToast = useCallback((message: string, kind: 'success' | 'error' | 'info') => {
    setToasts(prev => [...prev, { id: generateId(), message, kind }]);
  }, []);

  const removeToast = useCallback((id: string) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  const refreshSidebar = useCallback(async () => {
    const s = await getSources(runId).catch(() => []);
    const j = await getJobs(runId).catch(() => []);
    setSources(s.map((src: any) => ({ sourceId: src.source_id || generateId(), kind: src.kind || 'unknown', label: src.user_label || src.source_id || 'source', status: src.status || 'unknown' })));
    setJobs(j.map((job: any) => ({ jobId: job.subagent_run_id || job.job_id || generateId(), jobType: job.kind || 'unknown', status: job.status || 'unknown', sourceLabel: job.query || '' })));
  }, [runId]);

  // ── Real chat handler ──
  const handleSend = useCallback(async (text: string) => {
    const userMsg: Message = { id: generateId(), role: 'user', content: text, timestamp: Date.now() };
    setMessages(prev => [...prev, userMsg]);
    setProcessing(true);

    try {
      const rid = await initRun();
      const res = await sendChat(text, rid);

      const assistantMsg: Message = {
        id: generateId(), role: 'assistant',
        content: res.reply, timestamp: Date.now(),
      };
      setMessages(prev => [...prev, assistantMsg]);
      await refreshSidebar();
    } catch {
      setMessages(prev => [...prev, {
        id: generateId(), role: 'assistant',
        content: '抱歉，后端未启动。请运行: uv run uvicorn autoad_researcher.server.main:app --port 8000',
        timestamp: Date.now(),
      }]);
    }
    setProcessing(false);
  }, [initRun, refreshSidebar]);

  // ── Mock demo handlers (for visual testing) ──
  const playEvents = useCallback((events: WSMessage[], assistantId: string) => {
    setProcessing(true);
    let i = 0;
    let accumulatedContent = '';
    const updatedToolLines: ToolLine[] = [];

    const next = () => {
      if (i >= events.length) { setProcessing(false); return; }
      const evt = events[i++];
      if (evt.type === 'job.started') {
        const tl: ToolLine = { id: generateId(), text: `${evt.jobType === 'paper_parse' ? '解析' : evt.jobType === 'git_clone' ? 'clone' : evt.jobType === 'web_search' ? '搜索' : evt.jobType === 'web_fetch' ? '下载' : evt.jobType === 'repo_analyze' ? '分析' : '处理'}${evt.sourceLabel ? ` · ${evt.sourceLabel}` : ''}`, status: 'running' };
        setMessages(prev => prev.map(m => m.id === assistantId ? { ...m, toolLines: [...(m.toolLines || []), tl] } : m));
        updatedToolLines.push(tl);
      }
      if (evt.type === 'job.completed') {
        const idx = updatedToolLines.findIndex(tl => tl.status === 'running');
        if (idx >= 0) {
          updatedToolLines[idx] = { ...updatedToolLines[idx], status: 'done', duration: evt.duration };
          setMessages(prev => prev.map(m => m.id === assistantId ? { ...m, toolLines: [...updatedToolLines] } : m));
        }
      }
      if (evt.type === 'subagent.completed' && evt.toast) {
        addToast(evt.message || '完成', 'success');
      }
      if (evt.type === 'assistant.delta' && evt.content) {
        accumulatedContent += evt.content;
        setMessages(prev => prev.map(m => m.id === assistantId ? { ...m, content: accumulatedContent } : m));
      }
      setTimeout(next, evt.delay || 300);
    };
    setTimeout(next, 50);
  }, [addToast]);

  const handleFile = useCallback((name: string) => {
    const userMsg: Message = { id: generateId(), role: 'user', content: `📎 ${name}`, timestamp: Date.now() };
    setMessages(prev => [...prev, userMsg]);
    const assistantId = generateId();
    setMessages(prev => [...prev, { id: assistantId, role: 'assistant', content: '', timestamp: Date.now(), toolLines: [] }]);
    const { events } = mockParseFlow(name);
    playEvents(events, assistantId);
  }, [playEvents]);

  const handleUrl = useCallback((url: string) => {
    const userMsg: Message = { id: generateId(), role: 'user', content: `🔗 ${url}`, timestamp: Date.now() };
    setMessages(prev => [...prev, userMsg]);
    const assistantId = generateId();
    setMessages(prev => [...prev, { id: assistantId, role: 'assistant', content: '', timestamp: Date.now(), toolLines: [] }]);
    const { events } = mockUrlFlow(url);
    playEvents(events, assistantId);
  }, [playEvents]);

  const handleSearch = useCallback(() => {
    const userMsg: Message = { id: generateId(), role: 'user', content: '搜索 MVTec AD 最新方法', timestamp: Date.now() };
    setMessages(prev => [...prev, userMsg]);
    const assistantId = generateId();
    setMessages(prev => [...prev, { id: assistantId, role: 'assistant', content: '', timestamp: Date.now(), toolLines: [] }]);
    const { events } = mockSearchFlow('搜索 MVTec AD 最新方法');
    playEvents(events, assistantId);
  }, [playEvents]);

  const handleClone = useCallback(() => {
    handleUrl('https://github.com/amazon-science/patchcore-inspection');
  }, [handleUrl]);

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
      {showConfig && <ConfigModal config={config} onSave={saveConfig} onClose={closeConfig} />}
      <ToastContainer toasts={toasts} onRemove={removeToast} />

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 16px', borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
        <div style={{ fontWeight: 600, fontSize: '1.05em', color: 'var(--blue)' }}>AutoAD Researcher v2</div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span style={{ fontSize: '0.75em', color: 'var(--text-dim)' }}>{runId}</span>
          <DemoPanel onParsePdf={() => handleFile('2303.15140v2.pdf')} onUrl={handleUrl} onClone={handleClone} onSearch={handleSearch} onToast={addToast} />
          <button onClick={openConfig} title="配置 API Key" style={{ padding: '6px 10px' }}>⚙</button>
        </div>
      </div>

      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          <div style={{ flex: 1, overflowY: 'auto', padding: '12px 20px' }}>
            {messages.length === 0 && <WelcomeMessage />}
            {messages.map(msg =>
              msg.role === 'user' ? <UserMessage key={msg.id} msg={msg} /> : <AssistantMessage key={msg.id} msg={msg} />
            )}
            <div ref={bottomRef} />
          </div>
          <div style={{ padding: '0 16px', flexShrink: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <div style={{ flex: 1 }}><ChatInput onSend={handleSend} disabled={processing} /></div>
              <PlusMenu onFile={handleFile} />
            </div>
            <div className="kbd-hint">Enter 发送 · Shift+Enter 换行 · 🔔 演示看工具动画</div>
            <StatusBar sources={sources} jobs={jobs} evidenceCount={0} draftReady={false} />
          </div>
        </div>
        <Sidebar sources={sources} jobs={jobs} evidenceCount={0} draftReady={false} />
      </div>
    </div>
  );
}
