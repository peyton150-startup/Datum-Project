import { vi } from "vitest";

type Answer = { status?: number; body?: unknown };

/** Stub `fetch` with one responder and return the mock, so a test can assert
 *  on exactly which URLs and methods were requested. */
export function stubFetch(respond: (url: string, method: string) => Answer) {
  const mock = vi.fn((url: string, init?: RequestInit) => {
    const { status = 200, body = {} } = respond(url, init?.method ?? "GET");
    return Promise.resolve({
      ok: status >= 200 && status < 300, status, json: () => Promise.resolve(body),
    });
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}

export function requested(mock: ReturnType<typeof stubFetch>, method: string): string[] {
  return mock.mock.calls
    .filter(([, init]) => (init?.method ?? "GET") === method)
    .map(([url]) => url);
}
