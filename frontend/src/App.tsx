import { useState, useCallback, useEffect, useMemo, useRef } from 'react';
import { ChatInput } from './components/ChatInput';
import { FollowupQueue } from './components/FollowupQueue';
import { PlusMenu } from './components/PlusMenu';
import { UserMessage, AssistantMessage, WelcomeMessage } from './components/Messages';
import { ToastContainer } from './components/Toast';
import { ConfigModal } from './components/ConfigModal';
import { FirstRunSetup } from './components/FirstRunSetup';
import { StatusBar } from './components/StatusBar';
import { Sidebar } from './components/Sidebar';
import { LeftSidebar } from './components/LeftSidebar';
import { ExperimentPage } from './components/ExperimentPage';
import { SettingsPage } from './components/SettingsPage';
import { ReportPage } from './components/ReportPage';
import { DevMockPanel } from './components/DevMockPanel';
import { MarkdownContent } from './components/MarkdownContent';
import { TaskMenu } from './components/TaskMenu';
import { ExperimentTaskConfirmation } from './components/ExperimentTaskConfirmation';
import { useConfig } from './hooks/useConfig';
import { useAutoScroll } from './hooks/useAutoScroll';
import { useWebSocket } from './hooks/useWebSocket';
import {
  ApiError,
  confirmPrimaryMetrics,
  confirmExperimentTask,
  createRun,
  deleteSource,
  deleteRun,
  getArtifact,
  getEvidence,
  getEvidenceState,
  getIntentSummary,
  getJobs,
  getPendingExperimentTask,
  getRuns,
  getSources,
  getTranscript,
  renameRun,
  sendChat,
  uploadSource,
} from './lib/api';
import { generateId } from './lib/mock';
import type { Message, QueuedChatMessage, ToastItem, SourceItem, JobItem, EvidenceItem, UnusableParsedSource, WSMessage, PageId, TaskRun, IntentSummary, ExperimentTaskDraft } from './lib/types';

interface ArtifactEntry {
  path: string;
  label: string;
  content?: string;
}

interface PendingExperimentTaskConfirmation {
  runId: string;
  task: ExperimentTaskDraft;
}

function hasIntentSummary(summary: IntentSummary | null): boolean {
  return Boolean(
    summary
    && (
      summary.goal
      || summary.confirmed_facts.length
      || summary.inferred_facts.length
      || summary.unresolved_conflicts.length
      || summary.blocking_question
    )
  );
}

const MAX_VISIBLE_TOASTS = 3;

export default function App() {
  const { config, saveConfig, saveExperimentConfig, showConfig, openConfig, closeConfig, DEFAULT_EXPERIMENT } = useConfig();
  const [runId, setRunId] = useState<string>('');
  const [tasks, setTasks] = useState<TaskRun[]>([]);
  const [taskStatus, setTaskStatus] = useState<string>('Ready');
  const [messages, setMessages] = useState<Message[]>([]);
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const [sources, setSources] = useState<SourceItem[]>([]);
  const [pendingExperimentTaskConfirmation, setPendingExperimentTaskConfirmation] = useState<PendingExperimentTaskConfirmation | null>(null);
  const [jobs, setJobs] = useState<JobItem[]>([]);
  const [evidence, setEvidence] = useState<EvidenceItem[]>([]);
  const [unusableParsedSources, setUnusableParsedSources] = useState<UnusableParsedSource[]>([]);
  const [intentSummary, setIntentSummary] = useState<IntentSummary | null>(null);
  const [artifacts, setArtifacts] = useState<ArtifactEntry[]>([]);
  const [showDev, setShowDev] = useState(false);
  const [showExperimentSettings, setShowExperimentSettings] = useState(false);
  const [experimentRefreshTick, setExperimentRefreshTick] = useState(0);
  const [page, setPage] = useState<PageId>('chat');
  const [composerText, setComposerText] = useState('');
  const [queuedMessagesByRun, setQueuedMessagesByRun] = useState<Record<string, QueuedChatMessage[]>>({});
  const [queuePausedByRun, setQueuePausedByRun] = useState<Record<string, boolean>>({});
  const [runLoading, setRunLoading] = useState(false);
  const [loadedRunId, setLoadedRunId] = useState('');
  const currentRunIdRef = useRef('');
  const messagesRef = useRef<Message[]>([]);
  const activeChatTurnRunIdsRef = useRef(new Map<string, string>());
  const streamingHadDeltaIdsRef = useRef(new Set<string>());
  const completedAssistantIdsRef = useRef(new Set<string>());
  const drainingQueueRunIdRef = useRef<string | null>(null);
  const [chatTurnActive, setChatTurnActive] = useState(false);
  const bottomRef = useAutoScroll([messages]);
  const activeTask = tasks.find(task => task.run_id === runId) || null;
  const visibleTaskStatus = chatTurnActive ? 'Working' : taskStatus;
  const queuedMessages = useMemo(() => queuedMessagesByRun[runId] || [], [queuedMessagesByRun, runId]);
  const queuePaused = Boolean(queuePausedByRun[runId]);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  const addToast = useCallback((message: string, kind: 'success' | 'error' | 'info') => {
    setToasts(prev => [...prev, { id: generateId(), message, kind }].slice(-MAX_VISIBLE_TOASTS));
  }, []);

  const removeToast = useCallback((id: string) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  const refreshTasks = useCallback(async () => {
    const loaded = await getRuns(true).catch(() => []);
    setTasks(loaded);
    return loaded;
  }, []);

  const refreshSidebarForRun = useCallback(async (nextRunId: string) => {
    if (!nextRunId) return;
    const s = await getSources(nextRunId).catch(() => []);
    const j = await getJobs(nextRunId).catch(() => []);
    const evidenceState = await getEvidenceState(nextRunId).catch(() => ({ usable_evidence: [], unusable_parsed_sources: [] }));
    const summaryState = await getIntentSummary(nextRunId).catch(() => null);
    const e = Array.isArray(evidenceState.usable_evidence)
      ? evidenceState.usable_evidence
      : await getEvidence(nextRunId).catch(() => []);
    if (currentRunIdRef.current && currentRunIdRef.current !== nextRunId) return;
    setSources(s.map((src: any) => ({
      sourceId: src.source_id || generateId(),
      kind: src.kind || 'unknown',
      label: src.user_label || src.source_id || 'source',
      status: src.status || 'unknown',
      intakeStatus: typeof src.intake_status === 'string' ? src.intake_status : null,
    })));
    setJobs(j.map((job: any) => ({ jobId: job.job_id || generateId(), jobType: job.job_type || 'unknown', status: job.status || 'unknown', sourceLabel: job.outputs?.[0] || '', error: job.error || '' })));
    setEvidence(e.map(normalizeEvidence));
    setUnusableParsedSources((evidenceState.unusable_parsed_sources || []).map(normalizeUnusableParsedSource));
    setIntentSummary(summaryState);
  }, []);

  const refreshSidebar = useCallback(async () => {
    if (!runId) return;
    await refreshSidebarForRun(runId);
  }, [runId, refreshSidebarForRun]);

  const switchRun = useCallback(async (nextRunId: string) => {
    if (!nextRunId) return;
    setRunLoading(true);
    setLoadedRunId('');
    currentRunIdRef.current = nextRunId;
    setChatTurnActive([...activeChatTurnRunIdsRef.current.values()].some(value => value === nextRunId));
    setComposerText('');
    setRunId(nextRunId);
    setTaskStatus('Ready');
    setSources([]);
    setPendingExperimentTaskConfirmation(null);
    setJobs([]);
    setEvidence([]);
    setUnusableParsedSources([]);
    setIntentSummary(null);
    setArtifacts([]);
    setToasts([]);
    const transcript = await getTranscript(nextRunId).catch(() => []);
    if (currentRunIdRef.current !== nextRunId) return;
    setMessages(transcript.map(entry => ({
      id: generateId(),
      role: entry.role === 'user' ? 'user' : 'assistant',
      content: entry.content,
      timestamp: entry.created_at ? new Date(entry.created_at).getTime() : Date.now(),
    })));
    await refreshSidebarForRun(nextRunId);
    const pendingTask = await getPendingExperimentTask(nextRunId).catch(() => null);
    if (currentRunIdRef.current === nextRunId) {
      if (pendingTask?.status === 'pending_confirmation') {
        setPendingExperimentTaskConfirmation({ runId: nextRunId, task: pendingTask });
      }
      setLoadedRunId(nextRunId);
      setRunLoading(false);
    }
  }, [refreshSidebarForRun]);

  useEffect(() => {
    if (!config.apiKey) return;
    let cancelled = false;
    (async () => {
      const loaded = await getRuns(true).catch(() => []);
      if (cancelled) return;
      setTasks(loaded);
      const firstLive = loaded.find(task => !task.archived_at) || loaded[0];
      if (firstLive) {
        await switchRun(firstLive.run_id);
      } else {
        const created = await createRun().catch(() => null);
        if (cancelled || !created) return;
        setTasks([created]);
        await switchRun(created.run_id);
      }
    })();
    return () => { cancelled = true; };
  }, [config.apiKey, switchRun]);

  const handleCreateTask = useCallback(async () => {
    const created = await createRun().catch(() => null);
    if (!created) {
      addToast('创建任务失败', 'error');
      return;
    }
    await refreshTasks();
    await switchRun(created.run_id);
    addToast('任务已创建', 'success');
  }, [addToast, refreshTasks, switchRun]);

  const handleRenameTask = useCallback(async (title: string) => {
    if (!runId) return;
    const updated = await renameRun(runId, title).catch(() => null);
    if (!updated) {
      addToast('重命名失败', 'error');
      return;
    }
    setTasks(prev => prev.map(task => task.run_id === updated.run_id ? updated : task));
    addToast('任务已重命名', 'success');
  }, [addToast, runId]);

  const handleDeleteTask = useCallback(async (targetRunId: string) => {
    const ok = window.confirm('删除这个 session 会移除该任务目录，确认删除？');
    if (!ok) return;
    const deleted = await deleteRun(targetRunId).catch(() => null);
    if (!deleted) {
      addToast('删除失败', 'error');
      return;
    }
    setQueuedMessagesByRun(prev => {
      const next = { ...prev };
      delete next[targetRunId];
      return next;
    });
    setQueuePausedByRun(prev => {
      const next = { ...prev };
      delete next[targetRunId];
      return next;
    });
    const remaining = await refreshTasks();
    if (targetRunId === runId) {
      const nextTask = remaining.find(task => task.run_id !== targetRunId);
      if (nextTask) {
        await switchRun(nextTask.run_id);
      } else {
        const created = await createRun().catch(() => null);
        if (created) {
          await refreshTasks();
          await switchRun(created.run_id);
        } else {
          setRunId('');
          setMessages([]);
          setSources([]);
          setJobs([]);
          setEvidence([]);
          setUnusableParsedSources([]);
          setIntentSummary(null);
          setArtifacts([]);
        }
      }
    }
    addToast('任务已删除', 'success');
  }, [addToast, refreshTasks, runId, switchRun]);

  // ── First-run: create run on save ──
  const handleFirstRunSave = useCallback(async (c: typeof config) => {
    saveConfig(c);
    try {
      const r = await createRun();
      currentRunIdRef.current = r.run_id;
      setLoadedRunId(r.run_id);
      setRunId(r.run_id);
      setTasks([r]);
      setTaskStatus('Ready');
    } catch {
      currentRunIdRef.current = 'run_default';
      setLoadedRunId('run_default');
      setRunId('run_default');
    }
  }, [saveConfig]);

  // ── Real chat turn ──
  const runChatTurn = useCallback(async (text: string, targetRunId: string): Promise<boolean> => {
    const userMsg: Message = { id: generateId(), role: 'user', content: text, timestamp: Date.now() };
    const assistantId = generateId();
    const assistantMsg: Message = { id: assistantId, role: 'assistant', content: '', timestamp: Date.now() };
    const transcriptTail = messagesRef.current.slice(-12).map(msg => ({ role: msg.role, content: msg.content }));
    activeChatTurnRunIdsRef.current.set(assistantId, targetRunId);
    streamingHadDeltaIdsRef.current.delete(assistantId);
    completedAssistantIdsRef.current.delete(assistantId);
    if (currentRunIdRef.current === targetRunId) {
      setChatTurnActive(true);
      setTaskStatus('Working');
      setMessages(prev => [...prev, userMsg, assistantMsg]);
    }

    try {
      const res = await sendChat(text, targetRunId, assistantId, transcriptTail);
      if (
        currentRunIdRef.current === targetRunId
        && !streamingHadDeltaIdsRef.current.has(assistantId)
        && !completedAssistantIdsRef.current.has(assistantId)
      ) {
        setMessages(prev => prev.map(msg => (
          msg.id === assistantId ? { ...msg, content: res.reply } : msg
        )));
      }
      if (currentRunIdRef.current === targetRunId && res.source_action) {
        const action = res.source_action;
        const label = action.label_hint || action.source_id;
        const reason = action.reason ? `\n原因：${action.reason}` : '';
        const confirmed = window.confirm(`删除材料“${label}”及其 Evidence？${reason}`);
        if (confirmed) {
          const deleted = await deleteSource(targetRunId, action.source_id).catch(() => null);
          addToast(deleted ? '资料已删除' : '删除资料失败', deleted ? 'success' : 'error');
        }
      }
      if (currentRunIdRef.current === targetRunId && res.experiment_task) {
        const task = res.experiment_task;
        const goal = task.input_task.user_idea || task.input_task.request;
        if (task.status === 'confirmed') {
          const recover = window.confirm(
            `任务已按 ${task.execution_mode} 确认。是否恢复缺失的任务材料化？不会重新选择或升级执行模式。\n\n目标：${goal}`,
          );
          if (recover) {
            try {
              const prepared = await confirmExperimentTask(
                targetRunId,
                task.task_id,
                task.execution_mode,
              );
              addToast(`已恢复确认任务（${prepared.disposition}）`, 'success');
            } catch (error) {
              const message = error instanceof Error ? error.message : '任务恢复失败';
              addToast(`任务恢复失败：${message}`, 'error');
            }
          }
        } else {
          setPendingExperimentTaskConfirmation({ runId: targetRunId, task });
        }
      }
      if (currentRunIdRef.current === targetRunId) await refreshSidebarForRun(targetRunId);
      return true;
    } catch {
      if (currentRunIdRef.current === targetRunId) {
        setTaskStatus('Error');
        setMessages(prev => prev.map(msg => (
          msg.id === assistantId
            ? { ...msg, content: '抱歉，后端未启动。请运行: uv run uvicorn autoad_researcher.server.main:app --port 8000' }
            : msg
        )));
      }
      return false;
    } finally {
      activeChatTurnRunIdsRef.current.delete(assistantId);
      streamingHadDeltaIdsRef.current.delete(assistantId);
      if (currentRunIdRef.current === targetRunId) {
        const hasActiveTurn = [...activeChatTurnRunIdsRef.current.values()].some(value => value === targetRunId);
        setChatTurnActive(hasActiveTurn);
        if (!hasActiveTurn) setTaskStatus(current => current === 'Error' ? current : 'Ready');
      }
    }
  }, [addToast, refreshSidebarForRun]);

  const enqueueChatMessage = useCallback((text: string, targetRunId: string) => {
    const queued: QueuedChatMessage = {
      id: generateId(),
      runId: targetRunId,
      content: text,
      createdAt: Date.now(),
      status: 'queued',
    };
    setQueuedMessagesByRun(prev => ({
      ...prev,
      [targetRunId]: [...(prev[targetRunId] || []), queued],
    }));
  }, []);

  const handleSend = useCallback((text: string) => {
    const targetRunId = runId || 'run_default';
    const hasActiveTurn = [...activeChatTurnRunIdsRef.current.values()].some(value => value === targetRunId);
    const hasQueuedMessages = Boolean(queuedMessagesByRun[targetRunId]?.length);
    if (hasActiveTurn || hasQueuedMessages) {
      enqueueChatMessage(text, targetRunId);
      if (!hasActiveTurn && queuePausedByRun[targetRunId]) {
        setQueuePausedByRun(prev => ({ ...prev, [targetRunId]: false }));
      }
      return;
    }
    setQueuePausedByRun(prev => ({ ...prev, [targetRunId]: false }));
    void runChatTurn(text, targetRunId);
  }, [enqueueChatMessage, queuePausedByRun, queuedMessagesByRun, runChatTurn, runId]);

  const handleRestoreQueuedMessage = useCallback((id: string) => {
    const item = (queuedMessagesByRun[runId] || []).find(entry => entry.id === id);
    if (!item) return;
    setQueuedMessagesByRun(prev => ({
      ...prev,
      [runId]: (prev[runId] || []).filter(entry => entry.id !== id),
    }));
    setComposerText(current => current.trim() ? `${item.content}\n${current}` : item.content);
  }, [queuedMessagesByRun, runId]);

  useEffect(() => {
    const next = queuedMessages[0];
    if (!runId || loadedRunId !== runId || !next || runLoading || chatTurnActive || queuePaused) return;
    if ([...activeChatTurnRunIdsRef.current.values()].some(value => value === runId)) return;
    if (drainingQueueRunIdRef.current === runId) return;

    drainingQueueRunIdRef.current = runId;
    setQueuedMessagesByRun(prev => ({
      ...prev,
      [runId]: (prev[runId] || []).filter(entry => entry.id !== next.id),
    }));
    void runChatTurn(next.content, next.runId)
      .then(success => {
        if (!success) setQueuePausedByRun(prev => ({ ...prev, [runId]: true }));
      })
      .finally(() => {
        if (drainingQueueRunIdRef.current === runId) drainingQueueRunIdRef.current = null;
      });
  }, [chatTurnActive, loadedRunId, queuePaused, queuedMessages, runChatTurn, runId, runLoading]);

  // ── File upload — goes through real backend ──
  const handleFile = useCallback(async (file: File) => {
    const targetRunId = runId || 'run_default';
    setMessages(prev => [...prev, { id: generateId(), role: 'user', content: '📎 ' + file.name, timestamp: Date.now() }]);
    setTaskStatus('Working');
    try {
      const result = await uploadSource(targetRunId, file);
      const jobs = Array.isArray(result.jobs) ? result.jobs : [];
      const source = result.source || {};
      const reply = jobs.length
        ? `已上传 ${file.name}，已创建解析任务 ${jobs.map((job: any) => `\`${job.job_id}\``).join('、')}。解析完成后右侧 Evidence 会同步更新。`
        : `已上传 ${file.name}，已登记为可用文本资料，右侧 Evidence 已同步更新。`;
      setMessages(prev => [...prev, { id: generateId(), role: 'assistant', content: reply, timestamp: Date.now() }]);
      if (source.source_id) {
        setSources(prev => prev.some(s => s.sourceId === source.source_id)
          ? prev
          : [...prev, {
            sourceId: source.source_id,
            kind: source.kind || 'unknown',
            label: source.stored_path || file.name,
            status: 'uploaded_not_parsed',
            intakeStatus: typeof source.intake_status === 'string' ? source.intake_status : null,
          }]);
      }
      await refreshSidebarForRun(targetRunId);
      setTaskStatus('Ready');
    } catch {
      setTaskStatus('Error');
      setMessages(prev => [...prev, { id: generateId(), role: 'assistant', content: `上传失败：${file.name}` , timestamp: Date.now() }]);
      addToast('上传失败', 'error');
    }
  }, [addToast, refreshSidebarForRun, runId]);

  const handleDeleteSource = useCallback(async (sourceId: string) => {
    if (!runId) return;
    const ok = window.confirm('删除这个资料会移除对应 Source 和 Evidence，确认删除？');
    if (!ok) return;
    const deleted = await deleteSource(runId, sourceId).catch(() => null);
    if (!deleted) {
      addToast('删除资料失败', 'error');
      return;
    }
    await refreshSidebarForRun(runId);
    addToast('资料已删除', 'success');
  }, [addToast, refreshSidebarForRun, runId]);

  // ── WebSocket: real-time event handling ──
  const onWsMessage = useCallback((msg: WSMessage) => {
    if (msg.type.startsWith('experiment.')) {
      setExperimentRefreshTick(value => value + 1);
      return;
    }
    const jobId = msg.jobId || msg.job_id;
    const jobType = msg.jobType || msg.job_type;
    const sourceId = msg.sourceId || msg.source_id;
    const storedPath = msg.storedPath || msg.stored_path;
    if (msg.type === 'source.created') {
      if (!sourceId) return;
      setSources(prev => {
        if (prev.some(source => source.sourceId === sourceId)) return prev;
        return [...prev, { sourceId, kind: msg.kind || 'unknown', label: storedPath || sourceId, status: 'registered', intakeStatus: null }];
      });
    }
    if (msg.type === 'source.deleted') {
      if (sourceId) setSources(prev => prev.filter(source => source.sourceId !== sourceId));
      refreshSidebar();
    }
    if (msg.type === 'job.queued') {
      if (!jobId) return;
      setJobs(prev => {
        if (prev.some(job => job.jobId === jobId)) return prev;
        return [...prev, { jobId, jobType: jobType || 'unknown', status: 'queued' }];
      });
    }
    if (msg.type === 'job.started') {
      setJobs(prev => prev.map(j => j.jobId === jobId ? { ...j, status: 'running' } : j));
    }
    if (msg.type === 'job.completed') {
      setJobs(prev => prev.map(j => j.jobId === jobId ? { ...j, status: 'completed' } : j));
      setTaskStatus('Ready');
    }
    if (msg.type === 'job.failed') {
      setJobs(prev => prev.map(j => j.jobId === jobId ? { ...j, status: 'failed', error: msg.error || msg.message || j.error } : j));
      setTaskStatus('Error');
      refreshSidebar();
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
      refreshSidebar();
    }
    if (msg.type === 'evidence.updated') {
      refreshSidebar();
    }
    if (msg.type === 'assistant.delta' && msg.content) {
      const assistantId = msg.message_id;
      if (!assistantId || completedAssistantIdsRef.current.has(assistantId)) return;
      streamingHadDeltaIdsRef.current.add(assistantId);
      setMessages(prev => prev.map(m => (
        m.id === assistantId ? { ...m, content: m.content + msg.content } : m
      )));
    }
    if (msg.type === 'assistant.done') {
      const assistantId = msg.message_id;
      if (assistantId) completedAssistantIdsRef.current.add(assistantId);
      if (assistantId && typeof msg.content === 'string') {
        streamingHadDeltaIdsRef.current.add(assistantId);
        setMessages(prev => prev.map(m => (
          m.id === assistantId ? { ...m, content: msg.content || m.content } : m
        )));
      }
    }
    if (msg.type === 'toast.success' && msg.message) addToast(msg.message, 'success');
    if (msg.type === 'toast.error' && msg.message) addToast(msg.message, 'error');
  }, [addToast, refreshSidebar]);

  useWebSocket({ runId, onMessage: onWsMessage, enabled: !!runId });

  // ── FirstRunSetup when no API key ──
  if (!config.apiKey) {
    return <FirstRunSetup onSave={handleFirstRunSave} />;
  }

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
      {showConfig && <ConfigModal config={config} onSave={saveConfig} onClose={closeConfig} />}
      {pendingExperimentTaskConfirmation && (
        <ExperimentTaskConfirmation
          task={pendingExperimentTaskConfirmation.task}
          sources={sources}
          onClose={() => setPendingExperimentTaskConfirmation(null)}
          onConfirm={async (executionMode, executionRepositorySourceId) => {
            try {
              const prepared = await confirmExperimentTask(
                pendingExperimentTaskConfirmation.runId,
                pendingExperimentTaskConfirmation.task.task_id,
                executionMode,
                executionRepositorySourceId,
              );
              setPendingExperimentTaskConfirmation(null);
              addToast(`实验任务已确认（${prepared.disposition}）`, 'success');
              await refreshSidebarForRun(pendingExperimentTaskConfirmation.runId);
            } catch (error) {
              if (error instanceof ApiError && error.code === 'summary_changed') {
                addToast('研究摘要已在草案生成后更新；当前草案已过期。请先生成最新草案，再次确认。', 'info');
              }
              throw error;
            }
          }}
          onConfirmPrimaryMetrics={async primaryMetrics => {
            const updatedTask = await confirmPrimaryMetrics(
              pendingExperimentTaskConfirmation.runId,
              primaryMetrics,
            );
            setPendingExperimentTaskConfirmation(current => current && {
              ...current,
              task: updatedTask,
            });
            addToast('主指标已确认；请检查刷新后的任务草案后再确认执行。', 'success');
            await refreshSidebarForRun(pendingExperimentTaskConfirmation.runId);
          }}
        />
      )}
      <ToastContainer toasts={toasts} onRemove={removeToast} />

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 16px', borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 12 }}>
          <span style={{ fontWeight: 600, fontSize: '1.05em', color: 'var(--blue)' }}>AutoAD Researcher</span>
          <TaskMenu
            activeTask={activeTask}
            tasks={tasks}
            onSelect={switchRun}
            onCreate={handleCreateTask}
            onRename={handleRenameTask}
            onDelete={handleDeleteTask}
          />
          <span style={{
            fontSize: '0.75em', padding: '2px 8px', borderRadius: 4,
            background: visibleTaskStatus === 'Ready' ? '#1a3a1a' : visibleTaskStatus === 'Working' ? '#3a2a0a' : visibleTaskStatus === 'Error' ? '#3a1a1a' : '#1a1a3a',
            color: visibleTaskStatus === 'Ready' ? 'var(--green)' : visibleTaskStatus === 'Working' ? 'var(--orange)' : visibleTaskStatus === 'Error' ? 'var(--red)' : 'var(--blue)',
          }}>
            {visibleTaskStatus}
          </span>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <button onClick={openConfig} title="配置" style={{ padding: '6px 10px' }}>⚙</button>
        </div>
      </div>

      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
        <LeftSidebar page={page} onPage={nextPage => {
          setPage(nextPage);
          if (nextPage !== 'experiment') setShowExperimentSettings(false);
        }} />

        {page === 'chat' && (
          <>
            {/* Chat area */}
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
              <div style={{ flex: 1, overflowY: 'auto', padding: '12px 20px' }}>
                {messages.length === 0 && <WelcomeMessage />}
                {messages.map(msg =>
                  msg.role === 'user'
                    ? <UserMessage key={msg.id} msg={msg} />
                    : <AssistantMessage key={msg.id} msg={msg} />
                )}
                {messages.length > 0 && <div ref={bottomRef} />}
              </div>
              <div style={{ padding: '0 16px', flexShrink: 0 }}>
                <FollowupQueue
                  items={queuedMessages}
                  paused={queuePaused}
                  onRestore={handleRestoreQueuedMessage}
                />
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <div style={{ flex: 1 }}>
                    <ChatInput
                      value={composerText}
                      onChange={setComposerText}
                      onSend={handleSend}
                      disabled={runLoading}
                    />
                  </div>
                  <PlusMenu onFile={handleFile} />
                </div>
                <div className="kbd-hint">Enter 发送 · Shift+Enter 换行 · 粘贴 arXiv/GitHub 链接到输入框</div>
                <StatusBar sources={sources} jobs={jobs} evidenceCount={evidence.length} summaryAvailable={hasIntentSummary(intentSummary)} />
              </div>
            </div>

            {/* Right sidebar */}
            <Sidebar
              sources={sources}
              jobs={jobs}
              evidence={evidence}
              unusableParsedSources={unusableParsedSources}
              evidenceCount={evidence.length}
              summaryAvailable={hasIntentSummary(intentSummary)}
              intentSummary={intentSummary}
              onDeleteSource={handleDeleteSource}
            >
              {artifacts.length > 0 && (
                <div style={{ marginTop: 8 }}>
                  <div style={{ fontSize: '0.8em', color: 'var(--text-muted)', marginBottom: 6 }}>Markdown 摘要</div>
                  {artifacts.map(a => (
                    <ArtifactItem key={a.path} artifact={a} runId={runId} />
                  ))}
                </div>
              )}

              <div style={{ marginTop: 'auto', paddingTop: 12, borderTop: '1px solid var(--border)' }}>
                <button onClick={() => setShowDev(!showDev)} style={{ width: '100%', fontSize: '0.78em', padding: '4px 0', background: 'transparent', border: 'none', color: 'var(--text-dim)' }}>
                  {showDev ? '▼' : '▶'} 开发者详情
                </button>
                {showDev && (
                  <div style={{ fontSize: '0.72em', color: 'var(--text-dim)', marginTop: 4 }}>
                    <div>run_id: {runId || '未创建'}</div>
                    <div>资料：{sources.length} | 任务：{jobs.length}</div>
                    {artifacts.map(a => <div key={a.path}>产物：{a.path}</div>)}
                    {import.meta.env.DEV && (
                      <DevMockPanel addToast={addToast} setMessages={setMessages} />
                    )}
                  </div>
                )}
              </div>
            </Sidebar>
          </>
        )}

        {page === 'experiment' && (
          <ExperimentPage
            runId={runId}
            experimentRefreshTick={experimentRefreshTick}
            onOpenExperimentSettings={() => setShowExperimentSettings(true)}
            onDiscuss={text => {
              setShowExperimentSettings(false);
              setComposerText(text);
              setPage('chat');
            }}
          />
        )}

        {page === 'experiment' && showExperimentSettings && (
          <div role="dialog" aria-modal="true" aria-label="实验配置" style={{ position: 'fixed', inset: 0, zIndex: 20, overflow: 'auto', background: 'var(--bg)' }}>
            <SettingsPage
              experiment={config.experiment ?? DEFAULT_EXPERIMENT}
              defaultApiKey={config.apiKey}
              onSave={saveExperimentConfig}
              onBack={() => setShowExperimentSettings(false)}
              backLabel="返回工作台"
            />
          </div>
        )}

        {page === 'report' && (
          <ReportPage
            runId={runId}
            onBack={() => setPage('chat')}
          />
        )}
      </div>
    </div>
  );
}

function normalizeEvidence(item: any): EvidenceItem {
  return {
    sourceId: item.source_id || item.sourceId || '',
    artifactPath: item.artifact_path || item.artifactPath || '',
    evidenceType: item.evidence_type || item.evidenceType || 'evidence',
    supportLevel: item.support_level || item.supportLevel || 'supported',
    parserName: item.parser_name || item.parserName || '',
    summary: item.summary || '',
    raw: item.raw || {},
  };
}

function normalizeUnusableParsedSource(item: any): UnusableParsedSource {
  return {
    sourceId: item.source_id || item.sourceId || '',
    label: item.user_label || item.label || item.source_id || item.sourceId || 'source',
    status: item.status || 'failed',
    parseAttemptId: item.parse_attempt_id || item.parseAttemptId || '',
    parser: item.parser || '',
    warnings: Array.isArray(item.warnings) ? item.warnings : [],
    fatalErrors: Array.isArray(item.fatal_errors) ? item.fatal_errors : [],
    parserErrors: Array.isArray(item.parser_errors) ? item.parser_errors : [],
  };
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
          border: '1px solid var(--border)',
        }}>
          <MarkdownContent>{content.slice(0, 3000)}</MarkdownContent>
        </div>
      )}
    </div>
  );
}
