import { Check, Copy } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type { Message } from '../lib/types';
import { MarkdownContent } from './MarkdownContent';
import { ToolLineComponent } from './ToolLine';

async function copyText(content: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(content);
    return true;
  } catch {
    const textarea = document.createElement('textarea');
    textarea.value = content;
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    const copied = document.execCommand('copy');
    textarea.remove();
    return copied;
  }
}

function CopyMessageButton({ content }: { content: string }) {
  const [copied, setCopied] = useState(false);
  const resetTimerRef = useRef<number | null>(null);

  useEffect(() => () => {
    if (resetTimerRef.current !== null) window.clearTimeout(resetTimerRef.current);
  }, []);

  const handleCopy = async () => {
    if (!content || !await copyText(content)) return;
    setCopied(true);
    if (resetTimerRef.current !== null) window.clearTimeout(resetTimerRef.current);
    resetTimerRef.current = window.setTimeout(() => setCopied(false), 2000);
  };

  const label = copied ? '已复制' : '复制消息';
  return (
    <button type="button" className="message-copy-button" onClick={() => void handleCopy()} title={label} aria-label={label}>
      {copied ? <Check size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}
    </button>
  );
}

export function UserMessage({ msg }: { msg: Message }) {
  return (
    <div className="message">
      <div className="message-heading">
        <div className="msg-role user">You</div>
        <CopyMessageButton content={msg.content} />
      </div>
      <div className="msg-content">{msg.content}</div>
    </div>
  );
}

export function AssistantMessage({ msg, streaming = false }: { msg: Message; streaming?: boolean }) {
  return (
    <div className="message">
      <div className="message-heading">
        <div className="msg-role assistant">Assistant</div>
        {!streaming && msg.content && <CopyMessageButton content={msg.content} />}
      </div>
      {msg.toolLines?.map(tl => (
        <ToolLineComponent key={tl.id} tool={tl} />
      ))}
      {msg.content && (
        <MarkdownContent className={msg.toolLines?.length ? 'msg-content with-tool-lines' : 'msg-content'}>
          {msg.content}
        </MarkdownContent>
      )}
    </div>
  );
}

export function WelcomeMessage() {
  return (
    <div className="message welcome-message">
      <div className="msg-role assistant">Assistant</div>
      <div className="msg-content">
        <p>你好！我是 AutoAD Researcher，专门帮助你把异常检测或深度学习相关的研究想法、论文复现或实验构思整理清楚，然后输出可落地的研究方案。</p>

        <p><strong>你可以这样跟我协作：</strong></p>

        <p><strong>1. 告诉我你的研究目标</strong><br />
        比如：“我想改进 DeepSVDD 在图像异常检测上的效果”或“我想复现一篇我感兴趣的论文”。</p>

        <p><strong>2. 贴资料（论文链接、arXiv、GitHub 地址）</strong><br />
        我会帮你提取核心信息（任务、方法、数据集、指标），右侧 Evidence 区会显示摘要。</p>

        <p><strong>3. 补充关键信息</strong><br />
        如果你已经有 Baseline（比如 IGD、PatchCore）、目标数据集（比如 MVTec AD、VisA）、评估指标（AUROC、F1、AP）或成功标准，可以直接告诉我，我会帮你整理成一份“研究意图合同”，然后跟你确认。</p>

        <p><strong>4. 确认合同后，我会生成实验方案</strong><br />
        包括可能的改进方向、消融实验列表、baseline 配置等，方便你开始动手。</p>

        <p>现在你想从哪儿开始？告诉我你的研究背景、想做的方向，或者直接贴一个你想深入的文章链接都可以。</p>
      </div>
    </div>
  );
}
