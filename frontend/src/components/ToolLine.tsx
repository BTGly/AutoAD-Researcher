import type { ToolLine as ToolLineType } from '../lib/types';

export function ToolLineComponent({ tool }: { tool: ToolLineType }) {
  const dotClass = tool.status;
  const textClass = tool.status === 'done' ? 'done' : tool.status === 'error' ? 'error' : '';
  return (
    <div className="tool-line">
      <span className={`tool-dot ${dotClass}`}>
        {tool.status === 'running' ? '●' : tool.status === 'done' ? '✓' : tool.status === 'error' ? '✗' : 'ℹ'}
      </span>
      <span className={`tool-text ${textClass}`}>{tool.text}</span>
      {tool.duration && <span className="tool-duration">({tool.duration})</span>}
    </div>
  );
}
