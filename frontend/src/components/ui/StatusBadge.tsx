type Tone = 'neutral' | 'success' | 'warning' | 'danger' | 'info';

export function StatusBadge({ children, tone = 'neutral' }: { children: React.ReactNode; tone?: Tone }) {
  return <span className={`status-badge status-badge-${tone}`}>{children}</span>;
}
