/* DEV-only mock demo panel. Never included in production builds. */
import { DemoPanel } from './DemoPanel';

interface Props {
  addToast: (msg: string, kind: 'success' | 'error' | 'info') => void;
  setMessages: React.Dispatch<React.SetStateAction<any[]>>;
}

export function DevMockPanel({ addToast, setMessages }: Props) {
  const playEvents = async (fileName: string, kind: string) => {
    const { mockParseFlow, mockUrlFlow, mockSearchFlow, generateId } = await import('../lib/mock');
    let events;
    if (kind === 'parse') events = mockParseFlow(fileName).events;
    else if (kind === 'url') events = mockUrlFlow(fileName).events;
    else events = mockSearchFlow(fileName).events;

    const userMsg = { id: generateId(), role: 'user', content: kind === 'parse' ? '附件：' + fileName : kind === 'url' ? '链接：' + fileName : '搜索 ' + fileName, timestamp: Date.now() };
    setMessages((prev: any[]) => [...prev, userMsg]);
    const aid = generateId();
    const initial = events.find((e: any) => e.type === 'job.started');
    const jobText = initial ? (initial.jobType === 'paper_parse' ? '解析' : initial.jobType === 'git_clone' ? 'clone' : initial.jobType === 'web_fetch' ? '下载' : '处理') + (initial.sourceLabel ? ' · ' + initial.sourceLabel : '') : '';
    setMessages((prev: any[]) => [...prev, { id: aid, role: 'assistant', content: '', timestamp: Date.now(), toolLines: jobText ? [{ id: generateId(), text: jobText, status: 'running' }] : [] }]);

    let i = 1; let accumulated = '';
    const updatedLines: any[] = [];
    const next = () => {
      if (i >= events.length) return;
      const evt = events[i++];
      if (evt.type === 'job.completed') {
        const idx = updatedLines.findIndex((tl: any) => tl.status === 'running');
        if (idx >= 0) { updatedLines[idx] = { ...updatedLines[idx], status: 'done', duration: evt.duration }; }
        setMessages((prev: any[]) => prev.map((m: any) => m.id === aid ? { ...m, toolLines: [...updatedLines] } : m));
      }
      if (evt.type === 'subagent.completed' && evt.toast) addToast(evt.message || '完成', 'success');
      if (evt.type === 'assistant.delta' && evt.content) {
        accumulated += evt.content;
        setMessages((prev: any[]) => prev.map((m: any) => m.id === aid ? { ...m, content: accumulated } : m));
      }
      setTimeout(next, evt.delay || 300);
    };
    setTimeout(next, 50);
  };

  return (
    <div style={{ marginTop: 8 }}>
      <DemoPanel
        onParsePdf={() => playEvents('2303.15140v2.pdf', 'parse')}
        onUrl={(url: string) => playEvents(url, 'url')}
        onClone={() => playEvents('https://github.com/amazon-science/patchcore-inspection', 'url')}
        onSearch={() => playEvents('搜索 MVTec AD 最新方法', 'search')}
        onToast={addToast}
      />
    </div>
  );
}
