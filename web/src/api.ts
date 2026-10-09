export type Role = "viewer" | "operator" | "approver" | "admin";
export const allowed = (role: Role, minimum: Role) =>
  ["viewer", "operator", "approver", "admin"].indexOf(role) >=
  ["viewer", "operator", "approver", "admin"].indexOf(minimum);
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
export type SSEEvent = { id: string; event: string; data: string };
export class SSEParser {
  private decoder = new TextDecoder();
  private buffer = "";
  private id = "";
  constructor(private emit: (event: SSEEvent) => void) {}
  feed(bytes: Uint8Array) {
    this.buffer += this.decoder.decode(bytes, { stream: true });
    this.parse();
  }
  finish() {
    this.buffer += this.decoder.decode();
    this.parse();
  }
  private parse() {
    let match: RegExpExecArray | null;
    while ((match = /\r?\n\r?\n|\r\r/.exec(this.buffer))) {
      const block = this.buffer.slice(0, match.index);
      this.buffer = this.buffer.slice(match.index + match[0].length);
      let event = "message";
      const data: string[] = [];
      for (const line of block.split(/\r\n|\n|\r/)) {
        if (line.startsWith(":")) continue;
        const colon = line.indexOf(":");
        const field = colon < 0 ? line : line.slice(0, colon);
        let value = colon < 0 ? "" : line.slice(colon + 1);
        if (value.startsWith(" ")) value = value.slice(1);
        if (field === "data") data.push(value);
        if (field === "event") event = value;
        if (field === "id" && !value.includes("\0")) this.id = value;
      }
      if (data.length) this.emit({ id: this.id, event, data: data.join("\n") });
    }
  }
}
export class Api {
  private controllers = new Set<AbortController>();
  constructor(private key: string) {}
  close() {
    for (const c of this.controllers) c.abort();
    this.controllers.clear();
    this.key = "";
  }
  async request<T>(path: string, method = "GET", body?: unknown): Promise<T> {
    const c = new AbortController();
    this.controllers.add(c);
    try {
      const response = await fetch("/v1" + path, {
        method,
        headers: {
          "X-API-Key": this.key,
          ...(body === undefined ? {} : { "Content-Type": "application/json" }),
        },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: c.signal,
      });
      if (!response.ok) {
        let message = `Error ${response.status}`;
        try {
          const data = await response.json();
          message = typeof data.detail === "string" ? data.detail : message;
        } catch {}
        throw new ApiError(response.status, message);
      }
      return response.status === 204
        ? (undefined as T)
        : ((await response.json()) as T);
    } finally {
      this.controllers.delete(c);
    }
  }
  async stream(
    path: string,
    onEvent: (event: SSEEvent) => void,
    options: { body?: unknown; lastId?: string; signal?: AbortSignal } = {},
  ) {
    const c = new AbortController();
    this.controllers.add(c);
    const abort = () => c.abort();
    options.signal?.addEventListener("abort", abort);
    if (options.signal?.aborted) c.abort();
    try {
      const response = await fetch("/v1" + path, {
        method: options.body === undefined ? "GET" : "POST",
        headers: {
          "X-API-Key": this.key,
          Accept: "text/event-stream",
          ...(options.body === undefined
            ? {}
            : { "Content-Type": "application/json" }),
          ...(options.lastId ? { "Last-Event-ID": options.lastId } : {}),
        },
        body:
          options.body === undefined ? undefined : JSON.stringify(options.body),
        signal: c.signal,
      });
      if (!response.ok)
        throw new ApiError(
          response.status,
          `No se pudo abrir el stream (${response.status})`,
        );
      if (!response.body) throw new Error("Stream no disponible");
      const reader = response.body.getReader();
      const parser = new SSEParser(onEvent);
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          parser.feed(value);
        }
        parser.finish();
      } finally {
        await reader.cancel().catch(() => {});
        reader.releaseLock();
      }
    } finally {
      options.signal?.removeEventListener("abort", abort);
      this.controllers.delete(c);
    }
  }
}
