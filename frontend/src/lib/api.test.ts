import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError, renameRun } from './api';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('API errors', () => {
  it('retains the HTTP status and backend detail for rename failures', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(
      JSON.stringify({ detail: '任务名称不符合要求' }),
      { status: 422, headers: { 'Content-Type': 'application/json' } },
    )));

    let error: unknown;
    try {
      await renameRun('run_keep_this_id', '新标题');
    } catch (caught) {
      error = caught;
    }

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 422,
      message: '任务名称不符合要求',
      detail: '任务名称不符合要求',
    });
  });
});
