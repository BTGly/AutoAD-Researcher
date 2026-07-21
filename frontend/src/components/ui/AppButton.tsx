import type { ButtonHTMLAttributes, ReactNode } from 'react';

type Variant = 'primary' | 'secondary' | 'plain' | 'destructive';

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  children: ReactNode;
  variant?: Variant;
}

export function AppButton({ children, className = '', variant = 'secondary', type = 'button', ...props }: Props) {
  return <button type={type} className={`app-button app-button-${variant} ${className}`.trim()} {...props}>{children}</button>;
}
