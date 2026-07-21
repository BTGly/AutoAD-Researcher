import { Monitor, Moon, Sun } from 'lucide-react';
import { useTheme } from './ThemeContext';
import type { ThemePreference } from './theme';

const options: Array<{ preference: ThemePreference; label: string; icon: typeof Monitor }> = [
  { preference: 'system', label: '跟随系统外观', icon: Monitor },
  { preference: 'light', label: '浅色外观', icon: Sun },
  { preference: 'dark', label: '深色外观', icon: Moon },
];

export function ThemeToggle() {
  const { preference, setPreference } = useTheme();

  return (
    <div className="theme-toggle" role="group" aria-label="主题外观">
      {options.map(({ preference: option, label, icon: Icon }) => (
        <button
          key={option}
          type="button"
          className={`theme-toggle-option${preference === option ? ' active' : ''}`}
          aria-label={label}
          aria-pressed={preference === option}
          title={label}
          onClick={() => setPreference(option)}
        >
          <Icon size={15} strokeWidth={1.8} aria-hidden="true" />
        </button>
      ))}
    </div>
  );
}
