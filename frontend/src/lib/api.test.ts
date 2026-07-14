import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError, deleteRun, renameRun, sendChat, uploadSource } from './api';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('Run lifecycle requests', () => {
  it('passes AbortSignal to chat and upload requests', async () => {
    const fetchMock = vi.fn(async () => new Response(
      JSON.stringify({ reply: 'ok', reply_kind: 'answer', source: {}, jobs: [] }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    ));
    vi.stubGlobal('fetch', fetchMock);
    const controller = new AbortController();

    await sendChat('hello', 'run_active', 'request_1', [], controller.signal);
    await uploadSource('run_active', new File(['notes'], 'notes.md'), controller.signal);

    const calls = fetchMock.mock.calls as unknown as Array<[RequestInfo | URL, RequestInit]>;
    expect(calls[0]?.[1]).toMatchObject({ signal: controller.signal });
    expect(calls[1]?.[1]).toMatchObject({ signal: controller.signal });
  });

  it('accepts an asynchronous deleting response', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(
      JSON.stringify({ run_id: 'run_active', status: 'deleting', deleted: false }),
      { status: 202, headers: { 'Content-Type': 'application/json' } },
    )));

    await expect(deleteRun('run_active')).resolves.toMatchObject({ status: 'deleting' });
  });
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
