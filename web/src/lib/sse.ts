import type { JsonValue, LegacyEvent } from "../types/compat";

export interface SSEMessage {
  id: string;
  event: string;
  data: string;
}

export class SSEParser {
  private readonly decoder = new TextDecoder();
  private buffer = "";
  private lastId = "";

  constructor(private readonly emit: (event: SSEMessage) => void) {}

  feed(bytes: Uint8Array): void {
    this.buffer += this.decoder.decode(bytes, { stream: true });
    this.parse(false);
  }

  finish(): void {
    this.buffer += this.decoder.decode();
    this.parse(true);
  }

  private parse(flush: boolean): void {
    const separator = /\r\n\r\n|\n\n|\r\r/;
    let match = separator.exec(this.buffer);
    while (match) {
      this.consume(this.buffer.slice(0, match.index));
      this.buffer = this.buffer.slice(match.index + match[0].length);
      match = separator.exec(this.buffer);
    }
    if (flush && this.buffer.trim()) this.consume(this.buffer);
    if (flush) this.buffer = "";
  }

  private consume(block: string): void {
    let event = "message";
    const data: string[] = [];
    for (const line of block.split(/\r\n|\n|\r/)) {
      if (!line || line.startsWith(":")) continue;
      const colon = line.indexOf(":");
      const field = colon < 0 ? line : line.slice(0, colon);
      let value = colon < 0 ? "" : line.slice(colon + 1);
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "event") event = value;
      if (field === "data") data.push(value);
      if (field === "id" && !value.includes("\0")) this.lastId = value;
    }
    if (data.length) this.emit({ id: this.lastId, event, data: data.join("\n") });
  }
}

export interface TypedSSEEvent {
  id: string;
  event: LegacyEvent["type"] | string;
  data: LegacyEvent | JsonValue;
  raw: SSEMessage;
}

export interface StreamHandlers {
  onEvent?: (event: TypedSSEEvent) => void;
  onTerminal?: (event: TypedSSEEvent) => void;
}

export interface StreamRequest {
  method?: "GET" | "POST";
  body?: unknown;
  headers?: HeadersInit;
  lastEventId?: string;
}

const safeJson = (value: string): JsonValue => {
  try {
    return JSON.parse(value) as JsonValue;
  } catch {
    return value;
  }
};

export async function streamEvents(
  url: string,
  request: StreamRequest,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(url, {
    method: request.method ?? (request.body === undefined ? "GET" : "POST"),
    headers: {
      Accept: "text/event-stream",
      ...(request.body === undefined ? {} : { "Content-Type": "application/json" }),
      ...(request.lastEventId ? { "Last-Event-ID": request.lastEventId } : {}),
      ...request.headers,
    },
    body: request.body === undefined ? undefined : JSON.stringify(request.body),
    signal,
  });
  if (!response.ok) throw new Error(`No se pudo abrir el stream (${response.status})`);
  if (!response.body) throw new Error("El navegador no entregó el stream");

  const ids = new Set<string>();
  let terminal = false;
  const parser = new SSEParser((raw) => {
    if (raw.id && ids.has(raw.id)) return;
    if (raw.id) ids.add(raw.id);
    const event: TypedSSEEvent = {
      id: raw.id,
      event: raw.event,
      data: safeJson(raw.data),
      raw,
    };
    const terminalEvent = raw.event === "complete" || raw.event === "error";
    if (terminalEvent && terminal) return;
    handlers.onEvent?.(event);
    if (terminalEvent) {
      terminal = true;
      handlers.onTerminal?.(event);
    }
  });

  const reader = response.body.getReader();
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      parser.feed(value);
    }
    parser.finish();
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
