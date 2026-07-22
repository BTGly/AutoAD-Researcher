import { useState, useEffect, useCallback } from 'react';

const KEY = 'autoad_config';

export interface AppConfig {
  apiKey: string;
  baseUrl: string;
  dialogueModel: ModelId;
  reportModel: ModelId;
  experimentModel: ModelId;
}

export type ModelId = 'deepseek-v4-flash' | 'deepseek-v4-pro';

export const MODEL_OPTIONS: Array<{ value: ModelId; label: string }> = [
  { value: 'deepseek-v4-flash', label: 'DeepSeek V4 Flash' },
  { value: 'deepseek-v4-pro', label: 'DeepSeek V4 Pro' },
];

const LEGACY_MODEL_ALIASES: Record<string, ModelId> = {
  'deepseek-chat': 'deepseek-v4-flash',
  'deepseek-reasoner': 'deepseek-v4-pro',
};

const DEFAULTS: AppConfig = {
  apiKey: '',
  baseUrl: 'https://api.deepseek.com',
  dialogueModel: 'deepseek-v4-flash',
  reportModel: 'deepseek-v4-flash',
  experimentModel: 'deepseek-v4-pro',
};

function readConfig(): AppConfig {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || '{}') as Partial<AppConfig> & { model?: ModelId };
    return {
      ...DEFAULTS,
      ...raw,
      dialogueModel: normalizeStoredModel(raw.dialogueModel || raw.model, DEFAULTS.dialogueModel),
      reportModel: normalizeStoredModel(raw.reportModel, DEFAULTS.reportModel),
      experimentModel: normalizeStoredModel(raw.experimentModel, DEFAULTS.experimentModel),
    };
  } catch {
    return DEFAULTS;
  }
}

function normalizeStoredModel(value: string | undefined, fallback: ModelId): ModelId {
  const normalized = value ? LEGACY_MODEL_ALIASES[value] || value : '';
  return normalized === 'deepseek-v4-flash' || normalized === 'deepseek-v4-pro'
    ? normalized
    : fallback;
}

export function useConfig() {
  const [config, setConfig] = useState<AppConfig>(() => {
    return readConfig();
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
