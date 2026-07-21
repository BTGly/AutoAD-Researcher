import type { ButtonHTMLAttributes, ReactNode } from 'react';

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  children: ReactNode;
  label: string;
}

export function IconButton({ children, className = '', label, type = 'button', ...props }: Props) {
  return <button type={type} className={`icon-button ${className}`.trim()} aria-label={label} title={label} {...props}>{children}</button>;
}
