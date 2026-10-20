/**
 * The opt-in console and network recorder (heed#7): small ring buffers of what the
 * page logged and requested, kept in page memory so a report can carry the moments
 * before the visitor opened the widget.
 *
 * Privacy by construction:
 *   - nothing runs unless the embedder enabled `captureConsole` / `captureNetwork`;
 *   - every entry is masked as it is recorded (mask.ts), so nothing unmasked is kept,
 *     and request bodies, headers and responses are never read (metadata only);
 *   - nothing leaves the page unless the visitor ticks the box in the dialog, per
 *     report (element.ts).
 *
 * Wrapping is defensive: the page's own `console.*`, `fetch` and `XMLHttpRequest`
 * behave exactly as before, a recording failure is swallowed, and the original
 * promise / return value is passed through untouched.
 */
import { CONTRACT } from "./contract.gen.js";
import { maskText, maskUrl } from "./mask.js";

const L = CONTRACT.limits;

/** Mirrors heed's `LogEntry` model (base.py). */
export interface LogEntryPayload {
  level: string;
  message: string;
  at: string;
  source?: string;
}

/** Mirrors heed's `NetEntry` model (base.py): metadata only, never bodies. */
export interface NetEntryPayload {
  method: string;
  url: string;
  status: number | null;
  duration_ms: number | null;
  ok: boolean | null;
}

export interface Recorded {
  console?: LogEntryPayload[];
  network?: NetEntryPayload[];
}

export interface RecordKinds {
  console: boolean;
  network: boolean;
}

export interface RecorderOptions {
  /** Requests to leave out (heed's own POST of the report). */
  ignore?: (url: string) => boolean;
  /** Entries kept per buffer; the oldest go first. */
  maxEntries?: number;
}

export interface Recorder {
  /** Start recording `kinds` too (recording never narrows while the page lives). */
  enable(kinds: RecordKinds): void;
  /** Copies of the buffers for `kinds`. */
  snapshot(kinds: RecordKinds): Recorded;
  /** Restore what was wrapped (where nobody wrapped it again since) and stop. */
  stop(): void;
}

const LEVELS = ["log", "info", "warn", "error", "debug"] as const;
const MAX_STACK_LINES = 5;
const MAX_KEYS = 20;
const MAX_DEPTH = 3;
const SKIPPED_INITIATORS = new Set(["fetch", "xmlhttprequest", "beacon"]);

/** Lookbehind (used by the masking rules) is missing in old Safari: then nothing is recorded. */
export function canMask(): boolean {
  try {
    return maskText("x") === "x";
  } catch {
    return false;
  }
}

/** A bounded description of a console argument (no deep or huge serialisation on the page's hot path). */
export function describe(value: unknown, budget = L.log_message_chars * 2, depth = 0): string {
  if (typeof value === "string") return value.slice(0, budget);
  if (value === null || value === undefined || typeof value !== "object") {
    return typeof value === "function" ? `[function ${(value as { name?: string }).name || "anonymous"}]` : String(value);
  }
  if (value instanceof Error) {
    const stack = (value.stack ?? "").split("\n").slice(1, 1 + MAX_STACK_LINES).join("\n");
    return `${value.name}: ${value.message}${stack ? `\n${stack}` : ""}`.slice(0, budget);
  }
  const tag = (value as { nodeName?: unknown }).nodeName;
  if (typeof tag === "string") return `<${tag.toLowerCase()}>`;
  if (depth >= MAX_DEPTH) return Array.isArray(value) ? "[…]" : "{…}";
  let out = "";
  const add = (part: string) => {
    out += part;
    return out.length < budget;
  };
  try {
    if (Array.isArray(value)) {
      add("[");
      for (let i = 0; i < value.length && i < MAX_KEYS; i++) if (!add((i ? ", " : "") + describe(value[i], budget - out.length, depth + 1))) break;
      add(value.length > MAX_KEYS ? ", …]" : "]");
    } else {
      const keys = Object.keys(value);
      add("{");
      for (let i = 0; i < keys.length && i < MAX_KEYS; i++) {
        const v = (value as Record<string, unknown>)[keys[i]];
        if (!add(`${i ? ", " : ""}${keys[i]}: ${describe(v, budget - out.length, depth + 1)}`)) break;
      }
      add(keys.length > MAX_KEYS ? ", …}" : "}");
    }
  } catch {
    return "[unreadable]"; // a getter that throws, a revoked proxy
  }
  return out.slice(0, budget);
}

/**
 * Mask `raw`, then cut it to the server's limit. The raw text is bounded first (so a
 * huge log line costs little), dropping a run cut in half so no partial secret slips
 * under a rule's length threshold.
 */
export function maskMessage(raw: string, limit = L.log_message_chars): string {
  const bound = limit * 4;
  const bounded = raw.length > bound ? raw.slice(0, bound).replace(/\S*$/, "…") : raw;
  return maskText(bounded).slice(0, limit);
}

function ring<T>(max: number) {
  const items: T[] = [];
  return {
    push(item: T) {
      items.push(item);
      if (items.length > max) items.splice(0, items.length - max);
    },
    copy: () => items.slice(),
  };
}

const toUrl = (input: unknown): string =>
  typeof input === "string" ? input : input instanceof URL ? input.href : String((input as { url?: unknown })?.url ?? input);

export function startRecorder(win: Window, options: RecorderOptions = {}): Recorder {
  const max = options.maxEntries ?? L.log_entries;
  const ignore = options.ignore ?? (() => false);
  const logs = ring<LogEntryPayload>(max);
  const requests = ring<NetEntryPayload>(max);
  const restore: (() => void)[] = [];
  const enabled: RecordKinds = { console: false, network: false };
  let active = canMask();
  const base = win.location?.href;
  const now = () => win.performance?.now?.() ?? Date.now();

  /** `source` is already masked. */
  const log = (level: string, raw: string, source?: string) => {
    if (!active) return;
    try {
      const entry: LogEntryPayload = { level, message: maskMessage(raw), at: new Date().toISOString() };
      if (source) entry.source = source.slice(0, L.short_chars);
      logs.push(entry);
    } catch {
      // recording must never break the page
    }
  };
  const request = (method: string, url: string, started: number, status: number | null, ok: boolean | null) => {
    if (!active) return;
    try {
      if (ignore(new URL(url, base).href)) return;
      requests.push({
        method: maskText(method.toUpperCase()).slice(0, L.short_chars),
        url: maskUrl(url, base).slice(0, L.url_chars),
        status,
        duration_ms: Math.max(0, Math.round(now() - started)),
        ok,
      });
    } catch {
      // as above
    }
  };

  /** Replace `obj[key]` by `wrap(original)`; undone on stop only if still ours. */
  function patch<O extends object, K extends keyof O>(obj: O, key: K, wrap: (orig: O[K]) => O[K]) {
    const orig = obj[key];
    if (typeof orig !== "function") return;
    const ours = wrap(orig);
    obj[key] = ours;
    restore.push(() => {
      if (obj[key] === ours) obj[key] = orig;
    });
  }

  function recordConsole() {
    const c = (win as unknown as { console: Record<string, (...a: unknown[]) => unknown> }).console;
    for (const level of LEVELS) {
      patch(c, level, (orig) =>
        function (this: unknown, ...args: unknown[]) {
          if (active) log(level, args.map((a) => describe(a)).join(" "));
          return orig.apply(this, args);
        },
      );
    }
    const onError = (e: Event) => {
      if (!(e instanceof (win as unknown as { ErrorEvent: typeof ErrorEvent }).ErrorEvent)) return; // a resource that failed: the network buffer has it
      const at = e.filename ? `${maskUrl(e.filename, base)}:${e.lineno}:${e.colno}` : undefined;
      log("error", e.error instanceof Error ? describe(e.error) : e.message, at);
    };
    const onRejection = (e: PromiseRejectionEvent) => log("error", `Unhandled rejection: ${describe(e.reason)}`);
    win.addEventListener("error", onError);
    win.addEventListener("unhandledrejection", onRejection as EventListener);
    restore.push(() => {
      win.removeEventListener("error", onError);
      win.removeEventListener("unhandledrejection", onRejection as EventListener);
    });
  }

  function recordNetwork() {
    patch(win, "fetch", (orig) =>
      function (this: unknown, input: RequestInfo | URL, init?: RequestInit) {
        const started = now();
        const promise = (orig as typeof fetch).call(this ?? win, input, init);
        try {
          const req = typeof Request !== "undefined" && input instanceof Request ? input : null;
          const method = init?.method ?? req?.method ?? "GET";
          const url = toUrl(input);
          promise.then(
            (r) => request(method, url, started, r.status, r.ok),
            () => request(method, url, started, null, false),
          );
        } catch {
          // as above
        }
        return promise;
      } as typeof fetch,
    );
    const XHR = (win as unknown as { XMLHttpRequest?: typeof XMLHttpRequest }).XMLHttpRequest;
    if (XHR) {
      const opened = new WeakMap<XMLHttpRequest, { method: string; url: string }>();
      patch(XHR.prototype, "open", (orig) =>
        function (this: XMLHttpRequest, ...args: unknown[]) {
          try {
            opened.set(this, { method: String(args[0]), url: toUrl(args[1]) });
          } catch {
            // as above
          }
          return (orig as (...a: unknown[]) => void).apply(this, args);
        } as XMLHttpRequest["open"],
      );
      patch(XHR.prototype, "send", (orig) =>
        function (this: XMLHttpRequest, body?: Document | XMLHttpRequestBodyInit | null) {
          const meta = opened.get(this);
          if (meta) {
            const started = now();
            this.addEventListener("loadend", () => request(meta.method, meta.url, started, this.status || null, this.status ? this.status < 400 : false), { once: true });
          }
          return orig.call(this, body);
        },
      );
    }
    // Scripts, styles and images the page loads: from resource timing (no wrapping needed).
    const PO = (win as unknown as { PerformanceObserver?: typeof PerformanceObserver }).PerformanceObserver;
    if (PO) {
      try {
        const observer = new PO((list) => {
          for (const e of list.getEntries() as PerformanceResourceTiming[]) {
            if (SKIPPED_INITIATORS.has(e.initiatorType)) continue;
            const status = (e as PerformanceResourceTiming & { responseStatus?: number }).responseStatus || null;
            request("GET", e.name, now() - e.duration, status, status === null ? null : status < 400);
          }
        });
        observer.observe({ type: "resource", buffered: false });
        restore.push(() => observer.disconnect());
      } catch {
        // no resource timing here
      }
    }
  }

  const recorder: Recorder = {
    enable(kinds) {
      if (!active) return;
      if (kinds.console && !enabled.console) {
        enabled.console = true;
        recordConsole();
      }
      if (kinds.network && !enabled.network) {
        enabled.network = true;
        recordNetwork();
      }
    },
    snapshot(kinds) {
      const out: Recorded = {};
      if (kinds.console && enabled.console) out.console = logs.copy();
      if (kinds.network && enabled.network) out.network = requests.copy();
      return out;
    },
    stop() {
      active = false;
      for (const undo of restore.splice(0).reverse()) undo();
    },
  };
  return recorder;
}
