import { useState, useCallback, useRef, useEffect } from 'react';
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
import type { Message, ToastItem, ToolLine, SourceItem, JobItem } from './lib/types';
import { generateId, mockParseFlow, mockUrlFlow, mockSearchFlow } from './lib/mock';

export default function App() {
  const { config, saveConfig, showConfig, openConfig, closeConfig } = useConfig();
  const [messages, setMessages] = useState<Message[]>([]);
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const [sources, setSources] = useState<SourceItem[]>([]);
  const [jobs, setJobs] = useState<JobItem[]>([]);
  const [processing, setProcessing] = useState(false);
  const bottomRef = useAutoScroll([messages]);

  const addToast = useCallback((message: string, kind: 'success' | 'error' | 'info') => {
    const t: ToastItem = { id: generateId(), message, kind };
    setToasts(prev => [...prev, t]);
  }, []);

  const removeToast = useCallback((id: string) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  const playEvents = useCallback((events: import('./lib/types').WSMessage[], assistantId: string) => {
    setProcessing(true);
    let i = 0;
    let accumulatedContent = '';
    const updatedToolLines: ToolLine[] = [];

    const next = () => {
      if (i >= events.length) {
        setProcessing(false);
        return;
      }
      const evt = events[i++];

      if (evt.type === 'source.created') {
        setSources(prev => [...prev, { sourceId: evt.sourceId || generateId(), kind: evt.kind || 'unknown', label: evt.sourceLabel || evt.message || 'source', status: 'registered' }]);
      }
      if (evt.type === 'job.started') {
        const tl: ToolLine = { id: generateId(), text: `${evt.jobType === 'paper_parse' ? '解析' : evt.jobType === 'git_clone' ? 'clone' : evt.jobType === 'web_search' ? '搜索' : evt.jobType === 'web_fetch' ? '下载' : evt.jobType === 'repo_analyze' ? '分析' : '处理'}${evt.sourceLabel ? ` · ${evt.sourceLabel}` : ''}`, status: 'running' };
        setMessages(prev => prev.map(m => m.id === assistantId ? { ...m, toolLines: [...(m.toolLines || []), tl] } : m));
        updatedToolLines.push(tl);
        setJobs(prev => [...prev, { jobId: evt.jobId || generateId(), jobType: evt.jobType || 'unknown', status: 'running', sourceLabel: evt.sourceLabel }]);
      }
      if (evt.type === 'job.completed') {
        const idx = updatedToolLines.findIndex(tl => tl.status === 'running');
        if (idx >= 0) {
          updatedToolLines[idx] = { ...updatedToolLines[idx], status: 'done', duration: evt.duration };
          setMessages(prev => prev.map(m => m.id === assistantId ? { ...m, toolLines: [...updatedToolLines] } : m));
        }
        setJobs(prev => prev.map(j => j.jobId === evt.jobId ? { ...j, status: 'completed' } : j));
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

  const handleSend = useCallback((text: string) => {
    const userMsg: Message = { id: generateId(), role: 'user', content: text, timestamp: Date.now() };
    setMessages(prev => [...prev, userMsg]);
    const assistantId = generateId();
    const assistantMsg: Message = { id: assistantId, role: 'assistant', content: '', timestamp: Date.now(), toolLines: [] };
    setMessages(prev => [...prev, assistantMsg]);

    if (text.includes('http')) {
      const { events } = mockUrlFlow(text);
      playEvents(events, assistantId);
    } else if (text.includes('搜索')) {
      const { events } = mockSearchFlow(text);
      playEvents(events, assistantId);
    } else {
      setMessages(prev => prev.map(m => m.id === assistantId ? { ...m, content: '收到。当前暂无已解析的资料。\n\n试试点击右上角 🔔 演示 看完整模拟。' } : m));
    }
  }, [playEvents]);

  const handleFile = useCallback((name: string) => {
    const userMsg: Message = { id: generateId(), role: 'user', content: `📎 ${name}`, timestamp: Date.now() };
    setMessages(prev => [...prev, userMsg]);
    const assistantId = generateId();
    const assistantMsg: Message = { id: assistantId, role: 'assistant', content: '', timestamp: Date.now(), toolLines: [] };
    setMessages(prev => [...prev, assistantMsg]);
    const { events } = mockParseFlow(name);
    playEvents(events, assistantId);
  }, [playEvents]);

  const handleUrl = useCallback((url: string) => {
    const userMsg: Message = { id: generateId(), role: 'user', content: `🔗 ${url}`, timestamp: Date.now() };
    setMessages(prev => [...prev, userMsg]);
    const assistantId = generateId();
    const assistantMsg: Message = { id: assistantId, role: 'assistant', content: '', timestamp: Date.now(), toolLines: [] };
    setMessages(prev => [...prev, assistantMsg]);
    const { events } = mockUrlFlow(url);
    playEvents(events, assistantId);
  }, [playEvents]);

  const handleSearch = useCallback(() => {
    const userMsg: Message = { id: generateId(), role: 'user', content: '搜索 MVTec AD 最新方法', timestamp: Date.now() };
    setMessages(prev => [...prev, userMsg]);
    const assistantId = generateId();
    const assistantMsg: Message = { id: assistantId, role: 'assistant', content: '', timestamp: Date.now(), toolLines: [] };
    setMessages(prev => [...prev, assistantMsg]);
    const { events } = mockSearchFlow('搜索 MVTec AD 最新方法');
    playEvents(events, assistantId);
  }, [playEvents]);

  const handleClone = useCallback(() => {
    handleUrl('https://github.com/amazon-science/patchcore-inspection');
  }, [handleUrl]);

  const sidebarOpen = true;

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
      {showConfig && (
        <ConfigModal config={config} onSave={saveConfig} onClose={closeConfig} />
      )}

      <ToastContainer toasts={toasts} onRemove={removeToast} />

      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '10px 16px', borderBottom: '1px solid var(--border)', flexShrink: 0,
      }}>
        <div style={{ fontWeight: 600, fontSize: '1.05em', color: 'var(--blue)' }}>AutoAD Researcher v2</div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <DemoPanel onParsePdf={() => handleFile('2303.15140v2.pdf')} onUrl={handleUrl} onClone={handleClone} onSearch={handleSearch} onToast={addToast} />
          <button onClick={openConfig} title="配置 API Key" style={{ padding: '6px 10px' }}>
            ⚙
          </button>
        </div>
      </div>

      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
        <div style={{
          flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden',
        }}>
          <div style={{
            flex: 1, overflowY: 'auto', padding: '12px 20px',
          }}>
            {messages.length === 0 && <WelcomeMessage />}
            {messages.map(msg =>
              msg.role === 'user'
                ? <UserMessage key={msg.id} msg={msg} />
                : <AssistantMessage key={msg.id} msg={msg} />
            )}
            <div ref={bottomRef} />
          </div>

          <div style={{ padding: '0 16px', flexShrink: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <div style={{ flex: 1 }}>
                <ChatInput onSend={handleSend} disabled={processing} />
              </div>
              <PlusMenu onFile={handleFile} />
            </div>
            <div className="kbd-hint">Enter 发送 · Shift+Enter 换行</div>
            <StatusBar sources={sources} jobs={jobs} evidenceCount={2} draftReady={false} />
          </div>
        </div>

        {sidebarOpen && (
          <Sidebar sources={sources} jobs={jobs} evidenceCount={2} draftReady={false} />
        )}
      </div>
    </div>
  );
}
