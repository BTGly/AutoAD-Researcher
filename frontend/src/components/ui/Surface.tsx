import type { HTMLAttributes, ReactNode } from 'react';

export function Surface({ children, className = '', ...props }: HTMLAttributes<HTMLDivElement> & { children: ReactNode }) {
  return <div className={`surface ${className}`.trim()} {...props}>{children}</div>;
}
