import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClient, ApiError } from "./api-client";
import { SSEParser, streamEvents } from "./sse";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("ApiClient", () => {
  it("uses relative compatibility URLs and parses JSON responses", async () => {
    const fetcher = vi.fn(async () =>
      new Response(JSON.stringify([{ id: 7, name: "Value flow" }]), {
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetcher);

    const client = new ApiClient();
    await expect(client.flows.list()).resolves.toEqual([
      { id: 7, name: "Value flow" },
    ]);
    expect(fetcher).toHaveBeenCalledWith(
      "/flows/",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("joins an explicit deployment base without hard-coded hosts", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ ok: true })));
    vi.stubGlobal("fetch", fetcher);
    const client = new ApiClient({ baseUrl: "/fund-api/" });
    await client.ping();
    expect(fetcher).toHaveBeenCalledWith(
      "/fund-api/ping",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("surfaces a safe server detail and propagates abort", async () => {
    let signal: AbortSignal | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: unknown, init?: RequestInit) => {
        signal = init?.signal as AbortSignal;
        return await new Promise<Response>((_resolve, reject) => {
          signal?.addEventListener(
            "abort",
            () => reject(new DOMException("aborted", "AbortError")),
            { once: true },
          );
        });
      }),
    );
    const controller = new AbortController();
    const pending = new ApiClient().flows.list(controller.signal);
    controller.abort();
    expect(signal?.aborted).toBe(true);
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });

    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ detail: "El flujo ya existe" }), {
          status: 409,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    await expect(new ApiClient().flows.list()).rejects.toEqual(
      expect.objectContaining({
        status: 409,
        message: "El flujo ya existe",
      }),
    );
  });
});

describe("SSE compatibility", () => {
  it("parses CRLF, multiline data and chunks split inside UTF-8", () => {
    const events: unknown[] = [];
    const parser = new SSEParser((event) => events.push(event));
    const bytes = new TextEncoder().encode(
      'id: 2\r\nevent: progress\r\ndata: {"message":"análisis"}\r\ndata: final\r\n\r\n',
    );
    for (const byte of bytes) parser.feed(new Uint8Array([byte]));
    parser.finish();
    expect(events).toEqual([
      {
        id: "2",
        event: "progress",
        data: '{"message":"análisis"}\nfinal',
      },
    ]);
  });

  it("suppresses replayed IDs and calls the terminal handler once", async () => {
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          new TextEncoder().encode(
            'id: 1\nevent: progress\ndata: {"status":"Working"}\n\n' +
              'id: 1\nevent: progress\ndata: {"status":"Working"}\n\n' +
              'id: 2\nevent: complete\ndata: {"data":{}}\n\n' +
              'id: 3\nevent: complete\ndata: {"data":{}}\n\n',
          ),
        );
        controller.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(body)));
    const onEvent = vi.fn();
    const onTerminal = vi.fn();

    await streamEvents(
      "/hedge-fund/run",
      { method: "POST", body: { tickers: ["AAPL"] } },
      { onEvent, onTerminal },
    );

    expect(onEvent).toHaveBeenCalledTimes(2);
    expect(onTerminal).toHaveBeenCalledTimes(1);
  });
});
