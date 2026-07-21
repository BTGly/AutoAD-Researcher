import { defineConfig, devices } from '@playwright/test';

const runsRoot = process.env.AUTOAD_E2E_RUNS_ROOT;
if (!runsRoot) throw new Error('AUTOAD_E2E_RUNS_ROOT is required for the full-stack browser smoke');

const localBypass = '127.0.0.1,localhost';

export default defineConfig({
  testDir: './e2e',
  testMatch: 'fullstack-confirmation.spec.ts',
  timeout: 30_000,
  use: {
    baseURL: 'http://127.0.0.1:15173',
    ...devices['Desktop Chrome'],
  },
  webServer: [
    {
      command: '"${UV_BIN:-uv}" run --project .. uvicorn autoad_researcher.server.main:app --host 127.0.0.1 --port 18000',
      url: 'http://127.0.0.1:18000/api/health',
      reuseExistingServer: false,
      env: {
        ...process.env,
        AUTOAD_RUNS_ROOT: runsRoot,
        AUTOAD_EMBEDDED_WORKER: '0',
        NO_PROXY: localBypass,
        no_proxy: localBypass,
      },
    },
    {
      command: 'VITE_API_PROXY_TARGET=http://127.0.0.1:18000 npm run dev -- --host 127.0.0.1 --port 15173',
      url: 'http://127.0.0.1:15173',
      reuseExistingServer: false,
      env: { ...process.env, NO_PROXY: localBypass, no_proxy: localBypass },
    },
  ],
});
