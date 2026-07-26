import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react';

type Variant = 'primary' | 'secondary' | 'plain' | 'destructive';

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  children: ReactNode;
  variant?: Variant;
}

export const AppButton = forwardRef<HTMLButtonElement, Props>(function AppButton(
  { children, className = '', variant = 'secondary', type = 'button', ...props },
  ref,
) {
  return <button ref={ref} type={type} className={`app-button app-button-${variant} ${className}`.trim()} {...props}>{children}</button>;
});
