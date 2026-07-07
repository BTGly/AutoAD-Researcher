import { useState, useEffect, useCallback } from 'react';

const KEY = 'autoad_config';

export interface AppConfig {
  apiKey: string;
  baseUrl: string;
  model: string;
}

const DEFAULTS: AppConfig = {
  apiKey: '',
  baseUrl: 'https://api.deepseek.com',
  model: 'deepseek-v4-flash',
};

export function useConfig() {
  const [config, setConfig] = useState<AppConfig>(() => {
    try {
      const raw = localStorage.getItem(KEY);
      return raw ? { ...DEFAULTS, ...JSON.parse(raw) } : DEFAULTS;
    } catch {
      return DEFAULTS;
    }
  });
  const [showConfig, setShowConfig] = useState(!config.apiKey);

  const saveConfig = useCallback((c: AppConfig) => {
    localStorage.setItem(KEY, JSON.stringify(c));
    setConfig(c);
    setShowConfig(false);
  }, []);

  const openConfig = useCallback(() => setShowConfig(true), []);
  const closeConfig = useCallback(() => {
    if (config.apiKey) setShowConfig(false);
  }, [config.apiKey]);

  useEffect(() => {
    if (!config.apiKey) setShowConfig(true);
  }, [config.apiKey]);

  return { config, saveConfig, showConfig, openConfig, closeConfig };
}
