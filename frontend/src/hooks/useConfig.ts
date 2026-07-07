import { useState, useEffect, useCallback } from 'react';
import type { ExperimentConfig } from '../lib/types';

const KEY = 'autoad_config';

const DEFAULT_EXPERIMENT: ExperimentConfig = {
  provider: 'openai-chat',
  model: 'deepseek-v4-flash',
  apiKey: '',
  baseUrl: 'https://api.deepseek.com',
  reasoningEffort: 'high',
  maxCycles: 20,
  maxTurns: 50,
  executorTimeout: 172800,
  searchEnabled: false,
  autoSearch: false,
};

export interface AppConfig {
  apiKey: string;
  baseUrl: string;
  model: string;
  experiment?: ExperimentConfig;
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

  const saveExperimentConfig = useCallback((exp: ExperimentConfig) => {
    const next = { ...config, experiment: exp };
    localStorage.setItem(KEY, JSON.stringify(next));
    setConfig(next);
  }, [config]);

  const openConfig = useCallback(() => setShowConfig(true), []);
  const closeConfig = useCallback(() => {
    if (config.apiKey) setShowConfig(false);
  }, [config.apiKey]);

  useEffect(() => {
    if (!config.apiKey) setShowConfig(true);
  }, [config.apiKey]);

  return { config, saveConfig, saveExperimentConfig, showConfig, openConfig, closeConfig, DEFAULT_EXPERIMENT };
}
