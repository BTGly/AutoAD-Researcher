export const THEME_PREFERENCE_STORAGE_KEY = 'autoad_theme_preference';

export const themePreferences = ['system', 'light', 'dark'] as const;

export type ThemePreference = (typeof themePreferences)[number];
export type ResolvedTheme = Exclude<ThemePreference, 'system'>;

export function isThemePreference(value: unknown): value is ThemePreference {
  return typeof value === 'string' && themePreferences.includes(value as ThemePreference);
}

export function getStoredThemePreference(): ThemePreference {
  try {
    const value = window.localStorage.getItem(THEME_PREFERENCE_STORAGE_KEY);
    return isThemePreference(value) ? value : 'system';
  } catch {
    return 'system';
  }
}

export function resolveTheme(preference: ThemePreference, systemIsDark = window.matchMedia('(prefers-color-scheme: dark)').matches): ResolvedTheme {
  if (preference === 'system') return systemIsDark ? 'dark' : 'light';
  return preference;
}

export function applyTheme(preference: ThemePreference): ResolvedTheme {
  const resolved = resolveTheme(preference);
  const root = document.documentElement;
  root.dataset.theme = resolved;
  root.style.colorScheme = resolved;
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', resolved === 'dark' ? '#000000' : '#f5f5f7');
  return resolved;
}
