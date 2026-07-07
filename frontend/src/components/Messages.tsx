import type { Message } from '../lib/types';
import { ToolLineComponent } from './ToolLine';

export function UserMessage({ msg }: { msg: Message }) {
  return (
    <div className="message">
      <div className="msg-role user">You</div>
      <div className="msg-content">{msg.content}</div>
    </div>
  );
}

export function AssistantMessage({ msg }: { msg: Message }) {
  return (
    <div className="message">
      <div className="msg-role assistant">Assistant</div>
      {msg.toolLines?.map(tl => (
        <ToolLineComponent key={tl.id} tool={tl} />
      ))}
      {msg.content && <div style={{ marginTop: msg.toolLines?.length ? 8 : 0 }} className="msg-content">{msg.content}</div>}
    </div>
  );
}

export function WelcomeMessage() {
  return (
    <div className="message" style={{ textAlign: 'center', paddingTop: '20vh' }}>
      <div style={{ fontSize: '1.3em', marginBottom: 12, color: 'var(--blue)' }}>AutoAD Researcher v2</div>
      <div style={{ color: 'var(--text-muted)' }}>
        上传 PDF、粘贴 URL 或描述研究方向。<br />
        点击右上角 <span style={{ color: 'var(--blue)' }}>🔔 演示</span> 看完整模拟。
      </div>
    </div>
  );
}
