import { describe, expect, it, vi } from "vitest";
import { buildFormData, safeLink, sendReport, type SubmitInput } from "../src/submit";
import { defaultOptions, type HeedOptions } from "../src/options";
import { CONTRACT } from "../src/contract.gen";

const input = (over: Partial<SubmitInput> = {}): SubmitInput => ({
  draft: { category: "bug", title: "  Broken  ", body: "steps" },
  env: { viewport: "800x600", extra: {} },
  pageUrl: "https://a.example/p",
  honeypot: "",
  ...over,
});
const opts = (over: Partial<HeedOptions> = {}): HeedOptions => ({ ...defaultOptions(), endpoint: "https://h.example/heed/report", ...over });
const reply = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  vi.fn(async () => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } }));

describe("buildFormData", () => {
  it("uses the server's field names", () => {
    const f = buildFormData(input(), opts({ anonId: "abc" }), "tok");
    expect(f.get(CONTRACT.fields.title)).toBe("Broken");
    expect(f.get(CONTRACT.fields.category)).toBe("bug");
    expect(f.get(CONTRACT.fields.page_url)).toBe("https://a.example/p");
    expect(JSON.parse(f.get(CONTRACT.fields.env) as string)).toEqual({ viewport: "800x600", extra: {} });
    expect(f.get(CONTRACT.fields.anon_id)).toBe("abc");
    expect(f.get(CONTRACT.fields.bot_token)).toBe("tok");
    expect(f.get(CONTRACT.fields.honeypot!)).toBe("");
    expect(f.has(CONTRACT.fields.screenshot)).toBe(false);
  });
  it("stays within the server's caps", () => {
    const f = buildFormData(input({ draft: { category: "bug", title: "t".repeat(999), body: "b".repeat(99_999) } }), opts(), null);
    expect((f.get("title") as string).length).toBe(CONTRACT.limits.title_chars);
    expect((f.get("body") as string).length).toBe(CONTRACT.limits.body_chars);
    expect(f.has(CONTRACT.fields.bot_token)).toBe(false);
  });
  it("omits the honeypot when the server has none", () => {
    expect([...buildFormData(input(), opts({ honeypotField: "" }), null).keys()]).not.toContain(CONTRACT.fields.honeypot);
  });
});

describe("sendReport", () => {
  it("posts multipart with CORS and no credentials by default", async () => {
    const fetch = reply(200, { id: "r1", ok: true, ref: "1", url: "https://github.com/o/r/issues/1", status: "received" });
    const out = await sendReport(input(), opts(), fetch);
    expect(out).toEqual({ ok: true, id: "r1", status: "received", url: "https://github.com/o/r/issues/1" });
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://h.example/heed/report");
    expect(init.method).toBe("POST");
    expect(init.mode).toBe("cors");
    expect(init.credentials).toBe("omit");
    expect(init.body).toBeInstanceOf(FormData);
  });
  it("sends cookies only when asked", async () => {
    const fetch = reply(200, { id: "r1", ok: true, status: "received" });
    await sendReport(input(), opts({ withCredentials: true }), fetch);
    expect((fetch.mock.calls[0] as unknown as [string, RequestInit])[1].credentials).toBe("include");
  });
  it("reports a held report as held, without a link", async () => {
    const out = await sendReport(input(), opts(), reply(200, { id: "r", ok: true, ref: null, url: null, status: "held" }));
    expect(out).toEqual({ ok: true, id: "r", status: "held", url: null });
  });
  it("never links to a non-http URL", async () => {
    const out = await sendReport(input(), opts(), reply(200, { id: "r", ok: true, url: "javascript:alert(1)", status: "received" }));
    expect(out.ok && out.url).toBe(null);
    expect(safeLink("data:text/html,x")).toBe(null);
  });
  it.each([
    [413, { detail: "request body too large" }, "too_large"],
    [415, { detail: "screenshot must be a PNG, JPEG or WebP image" }, "invalid"],
    [422, { detail: [{ loc: ["title"] }] }, "invalid"],
    [403, { detail: "bot verification failed" }, "bot"],
    [403, { detail: "origin not allowed" }, "forbidden"],
    [502, { detail: "the report could not be delivered" }, "unavailable"],
    [200, { id: "r", ok: false }, "unavailable"],
  ])("maps HTTP %i to %s", async (status, body, kind) => {
    const out = await sendReport(input(), opts(), reply(status, body));
    expect(out).toEqual({ ok: false, kind, retryAfterSeconds: null });
  });
  it("reads Retry-After on rate limits and busy answers", async () => {
    expect(await sendReport(input(), opts(), reply(429, { detail: "rate limit exceeded" }, { "Retry-After": "60" }))).toEqual({ ok: false, kind: "rate_limited", retryAfterSeconds: 60 });
    expect(await sendReport(input(), opts(), reply(503, { detail: "busy" }, { "Retry-After": "2" }))).toEqual({ ok: false, kind: "unavailable", retryAfterSeconds: 2 });
  });
  it("calls the bot-token seam and sends its token", async () => {
    const fetch = reply(200, { id: "r", ok: true, status: "received" });
    await sendReport(input(), opts({ botToken: async () => "turnstile-token" }), fetch);
    const body = (fetch.mock.calls[0] as unknown as [string, RequestInit])[1].body as FormData;
    expect(body.get("bot_token")).toBe("turnstile-token");
  });
  it("does not send when the bot-token seam fails", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const fetch = reply(200, {});
    const out = await sendReport(input(), opts({ botToken: () => { throw new Error("no widget"); } }), fetch);
    expect(out).toEqual({ ok: false, kind: "bot", retryAfterSeconds: null });
    expect(fetch).not.toHaveBeenCalled();
    warn.mockRestore();
  });
  it("tells a network failure from a timeout", async () => {
    const down = vi.fn(async () => { throw new TypeError("Failed to fetch"); });
    expect(await sendReport(input(), opts(), down)).toEqual({ ok: false, kind: "network", retryAfterSeconds: null });
    const hang = vi.fn((_u: string, init: RequestInit) => new Promise<Response>((_, reject) => init.signal!.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")))));
    expect(await sendReport(input(), opts({ timeoutMs: 10 }), hang as unknown as typeof fetch)).toEqual({ ok: false, kind: "timeout", retryAfterSeconds: null });
  });
  it("times out on a server that stalls after its headers", async () => {
    const stalled = vi.fn(async () => new Response(new ReadableStream({ start() {} }), { status: 200 }));
    expect(await sendReport(input(), opts({ timeoutMs: 20 }), stalled)).toEqual({ ok: false, kind: "timeout", retryAfterSeconds: null });
  });
  it("reports a bot-token provider that never answers as the bot error, not a slow server, without sending", async () => {
    const fetch = reply(200, {});
    const out = await sendReport(input(), opts({ timeoutMs: 20, botToken: () => new Promise<string>(() => {}) }), fetch);
    expect(out).toEqual({ ok: false, kind: "bot", retryAfterSeconds: null });
    expect(fetch).not.toHaveBeenCalled();
  });
});
