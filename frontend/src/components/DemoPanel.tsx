import { useState } from 'react';

interface Props {
  onParsePdf: () => void;
  onUrl: (url: string) => void;
  onClone: () => void;
  onSearch: () => void;
  onToast: (message: string, kind: 'success' | 'error' | 'info') => void;
}

export function DemoPanel({ onParsePdf, onUrl, onClone, onSearch, onToast }: Props) {
  const [open, setOpen] = useState(false);

  return (
    <div style={{ position: 'relative', display: 'inline-block' }}>
      <button onClick={() => setOpen(!open)} style={{ background: 'var(--orange)', color: '#000', border: 'none', fontWeight: 600 }}>
        🔔 演示
      </button>
      {open && (
        <>
          <div style={{ position: 'fixed', inset: 0, zIndex: 99 }} onClick={() => setOpen(false)} />
          <div style={{
            position: 'absolute', top: '110%', right: 0, zIndex: 100,
            background: 'var(--bg-panel)', border: '1px solid var(--border)',
            borderRadius: 10, padding: 16, width: 260,
          }}>
            <div style={{ fontSize: '0.82em', color: 'var(--text-muted)', marginBottom: 10 }}>点一下看完整模拟</div>
            <button onClick={() => { onParsePdf(); setOpen(false); }} style={{ width: '100%', marginBottom: 6 }}>
              📄 模拟解析 PDF
            </button>
            <button onClick={() => { onUrl('https://arxiv.org/abs/2303.15140'); setOpen(false); }} style={{ width: '100%', marginBottom: 6 }}>
              🔗 模拟下载 arXiv
            </button>
            <button onClick={() => { onClone(); setOpen(false); }} style={{ width: '100%', marginBottom: 6 }}>
              📦 模拟 clone GitHub
            </button>
            <button onClick={() => { onSearch(); setOpen(false); }} style={{ width: '100%', marginBottom: 12 }}>
              🔍 模拟搜索论文
            </button>
            <div style={{ fontSize: '0.82em', color: 'var(--text-muted)', marginBottom: 8 }}>Toast 演示</div>
            <div style={{ display: 'flex', gap: 6 }}>
              <button onClick={() => { onToast('PDF 解析完成 · paper_brief.md 已生成', 'success'); setOpen(false); }} style={{ flex: 1, fontSize: '0.8em' }}>
                ✅ 成功
              </button>
              <button onClick={() => { onToast('PDF 解析失败 · 文件可能为扫描件', 'error'); setOpen(false); }} style={{ flex: 1, fontSize: '0.8em' }}>
                ❌ 失败
              </button>
              <button onClick={() => { onToast('找到 5 个候选来源', 'info'); setOpen(false); }} style={{ flex: 1, fontSize: '0.8em' }}>
                ℹ️ 信息
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
