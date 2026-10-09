import { describe, it, expect, vi } from "vitest";
import { SSEParser, allowed } from "./api";
describe("SSE parser", () => {
  it("decodes split UTF8, CRLF, multiline and IDs once", () => {
    const events: unknown[] = [];
    const parser = new SSEParser((e) => events.push(e));
    const bytes = new TextEncoder().encode(
      "id: 8\r\nevent: stage\r\ndata: café\r\ndata: listo\r\n\r\n",
    );
    for (const b of bytes) parser.feed(new Uint8Array([b]));
    parser.finish();
    expect(events).toEqual([{ id: "8", event: "stage", data: "café\nlisto" }]);
  });
  it("ignores comments and empty records", () => {
    const events: unknown[] = [];
    const parser = new SSEParser((e) => events.push(e));
    parser.feed(new TextEncoder().encode(":ping\n\ndata: ok\n\n"));
    expect(events).toEqual([{ id: "", event: "message", data: "ok" }]);
  });
});
it("gates operations and approval independently by role", () => {
  expect(allowed("viewer", "operator")).toBe(false);
  expect(allowed("operator", "approver")).toBe(false);
  expect(allowed("approver", "operator")).toBe(true);
  expect(allowed("admin", "admin")).toBe(true);
});
it("cancels a live stream when a consumer fails and never repeats the POST", async () => {
  const { Api } = await import("./api");
  const cancel = vi.fn();
  const fetcher = vi.fn(
    async () =>
      new Response(
        new ReadableStream({
          start(c) {
            c.enqueue(new TextEncoder().encode("data: broken\n\n"));
          },
          cancel,
        }),
      ),
  );
  vi.stubGlobal("fetch", fetcher);
  const api = new Api("secret");
  await expect(
    api.stream(
      "/chat/threads/x/messages",
      () => {
        throw new Error("consumer");
      },
      { body: { message: "hello" } },
    ),
  ).rejects.toThrow("consumer");
  expect(cancel).toHaveBeenCalledOnce();
  expect(fetcher).toHaveBeenCalledOnce();
  api.close();
  vi.unstubAllGlobals();
});
it("clears the session key and aborts every active request on logout", async () => {
  const { Api } = await import("./api");
  let requestSignal: AbortSignal | undefined;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: unknown, init?: RequestInit) => {
      requestSignal = init?.signal as AbortSignal;
      return await new Promise<Response>((_resolve, reject) =>
        requestSignal?.addEventListener(
          "abort",
          () => reject(new DOMException("aborted", "AbortError")),
          { once: true },
        ),
      );
    }),
  );
  const api = new Api("session-key");
  const pending = api.request("/threads");
  api.close();
  expect(requestSignal?.aborted).toBe(true);
  await expect(pending).rejects.toThrow("aborted");
  vi.unstubAllGlobals();
});
