import { useRef, useState } from 'react';

interface Props {
  onFile: (file: File) => void;
}

export function PlusMenu({ onFile }: Props) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);

  const handleFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) {
      onFile(f);
      e.target.value = '';
      setOpen(false);
    }
  };

  return (
    <div style={{ position: 'relative' }}>
      <button onClick={() => setOpen(!open)} style={{ padding: '6px 10px', fontSize: '1.2em' }} title="上传文件">
        +
      </button>
      {open && (
        <>
          <div style={{ position: 'fixed', inset: 0, zIndex: 99 }} onClick={() => setOpen(false)} />
          <div style={{
            position: 'absolute', bottom: '110%', right: 0, zIndex: 100,
            background: 'var(--bg-panel)', border: '1px solid var(--border)',
            borderRadius: 10, padding: 16, width: 200,
          }}>
            <div style={{ fontSize: '0.82em', color: 'var(--text-muted)', marginBottom: 10 }}>上传资料</div>
            <input ref={fileRef} type="file" accept=".pdf,.txt,.md,.markdown" onChange={handleFile} style={{ display: 'none' }} />
            <button onClick={() => fileRef.current?.click()} style={{ width: '100%' }}>
              📄 选择 PDF / txt / md
            </button>
          </div>
        </>
      )}
    </div>
  );
}
