import { useRef, useState } from 'react';
import { FileText, Plus } from 'lucide-react';
import { AppButton } from './ui/AppButton';
import { IconButton } from './ui/IconButton';
import { usePresence } from '../hooks/usePresence';

interface Props {
  onFile: (file: File) => void;
}

export function PlusMenu({ onFile }: Props) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const { present, state } = usePresence(open, 180);

  const handleFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) {
      onFile(f);
      e.target.value = '';
      setOpen(false);
    }
  };

  return (
    <div className={`plus-menu${open ? ' open' : ''}`}>
      <IconButton onClick={() => setOpen(!open)} label="上传文件"><Plus size={18} aria-hidden="true" /></IconButton>
      {present && (
        <>
          <div className="plus-menu-scrim" data-state={state} aria-hidden="true" onClick={() => setOpen(false)} />
          <div className="plus-menu-popover" data-state={state} aria-hidden={!open}>
            <div className="plus-menu-title">上传资料</div>
            <input ref={fileRef} type="file" accept=".pdf,.txt,.md,.markdown" onChange={handleFile} style={{ display: 'none' }} />
            <AppButton onClick={() => fileRef.current?.click()} style={{ width: '100%' }}><FileText size={16} aria-hidden="true" />选择 PDF / txt / md</AppButton>
          </div>
        </>
      )}
    </div>
  );
}
