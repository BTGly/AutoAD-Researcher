interface Props {
  runId: string;
  onOpenExperimentSettings: () => void;
}

/**
 * The observatory deliberately owns no experiment state.  Later increments
 * replace this shell with a read-only projection from durable run artifacts.
 */
export function ExperimentPage({ runId, onOpenExperimentSettings }: Props) {
  return (
    <main style={{ flex: 1, minWidth: 0, overflow: 'auto', padding: 24 }}>
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        gap: 16, marginBottom: 20,
      }}>
        <div>
          <h1 style={{ margin: 0, fontSize: '1.25em', color: 'var(--text)' }}>实验工作台</h1>
          <p style={{ margin: '6px 0 0', fontSize: '0.84em', color: 'var(--text-muted)' }}>
            只读展示当前实验的持久化状态和结果。
          </p>
        </div>
        <button onClick={onOpenExperimentSettings} style={{ padding: '7px 12px' }}>
          实验配置
        </button>
      </div>

      {!runId ? (
        <EmptyState title="请先创建一个研究任务。" />
      ) : (
        <EmptyState title="实验尚未启动。请先在“研究助手”中确认实验任务。" />
      )}
    </main>
  );
}

function EmptyState({ title }: { title: string }) {
  return (
    <section style={{
      minHeight: 280, display: 'grid', placeItems: 'center', textAlign: 'center',
      border: '1px solid var(--border)', borderRadius: 8, color: 'var(--text-muted)',
      background: 'var(--bg-panel)', padding: 24,
    }}>
      <div>
        <div style={{ fontSize: '2em', marginBottom: 12 }}>🔬</div>
        <div>{title}</div>
      </div>
    </section>
  );
}
