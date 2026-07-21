import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { applyTheme, getStoredThemePreference, THEME_PREFERENCE_STORAGE_KEY, type ThemePreference } from './theme';
import { ThemeContext, type ThemeContextValue } from './ThemeContext';

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPreference] = useState<ThemePreference>(getStoredThemePreference);

  useEffect(() => {
    applyTheme(preference);
    if (preference !== 'system') return;
    const query = window.matchMedia('(prefers-color-scheme: dark)');
    const updateSystemTheme = () => applyTheme('system');
    query.addEventListener('change', updateSystemTheme);
    return () => query.removeEventListener('change', updateSystemTheme);
  }, [preference]);

  const value = useMemo<ThemeContextValue>(() => ({
    preference,
    setPreference: next => {
      try {
        window.localStorage.setItem(THEME_PREFERENCE_STORAGE_KEY, next);
      } catch {
        // A denied storage policy must not prevent visual theme selection.
      }
      setPreference(next);
    },
  }), [preference]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}
