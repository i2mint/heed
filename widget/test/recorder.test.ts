import { afterEach, describe, expect, it, vi } from "vitest";
import { describe as describeArg, maskMessage, startRecorder } from "../src/recorder";
import { fitJson } from "../src/submit";

const recorders: { stop(): void }[] = [];
const start = (...args: Parameters<typeof startRecorder>) => {
  const r = startRecorder(...args);
  recorders.push(r);
  return r;
};
afterEach(() => {
  for (const r of recorders.splice(0)) r.stop();
  vi.unstubAllGlobals();
});

describe("startRecorder", () => {
  it("records nothing until enabled, then masked console entries", () => {
    const r = start(window);
    console.info("before");
    expect(r.snapshot({ console: true, network: true })).toEqual({});
    r.enable({ console: true, network: false });
    console.warn("login for ana@example.com", { token: "abc123" });
    const { console: logs } = r.snapshot({ console: true, network: false });
    expect(logs).toHaveLength(1);
    expect(logs![0].level).toBe("warn");
    expect(logs![0].message).toBe('login for [email] {"token": "[redacted]"}');
    expect(logs![0].message).not.toContain("abc123");
  });

  it("keeps the page's console working and restores it on stop", () => {
    const seen: unknown[] = [];
    const original = console.log;
    console.log = (...a: unknown[]) => void seen.push(a);
    try {
      const r = start(window);
      r.enable({ console: true, network: false });
      console.log("hello", 1);
      expect(seen).toEqual([["hello", 1]]);
      r.stop();
      expect(console.log).not.toBe(original); // our stand-in, not the recorder's wrapper
      console.log("after");
      expect(r.snapshot({ console: true, network: false }).console).toHaveLength(1);
    } finally {
      console.log = original;
    }
  });

  it("records page errors and unhandled rejections", () => {
    const r = start(window);
    r.enable({ console: true, network: false });
    window.dispatchEvent(new ErrorEvent("error", { message: "boom", filename: "https://app.example/a.js?v=1", lineno: 3, colno: 7 }));
    const rejection = new Event("unhandledrejection") as PromiseRejectionEvent;
    Object.defineProperty(rejection, "reason", { value: new Error("nope") });
    window.dispatchEvent(rejection);
    const logs = r.snapshot({ console: true, network: false }).console!;
    expect(logs[0]).toMatchObject({ level: "error", message: "boom", source: "https://app.example/a.js?v=[redacted]:3:7" });
    expect(logs[1].message).toContain("Unhandled rejection: Error: nope");
  });

  it("keeps the newest entries within its size", () => {
    const r = start(window, { maxEntries: 3 });
    r.enable({ console: true, network: false });
    for (let i = 0; i < 5; i++) console.debug(`n${i}`);
    expect(r.snapshot({ console: true, network: false }).console!.map((e) => e.message)).toEqual(["n2", "n3", "n4"]);
  });

  it("records fetch metadata (masked URL, status) but not heed's own report", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 404 })));
    const r = start(window, { ignore: (url) => url.startsWith("https://h.example/heed/report") });
    r.enable({ console: false, network: true });
    const response = await fetch("https://api.example/users/12345?session=abc", { method: "post", body: "secret body" });
    expect(response.status).toBe(404); // the page still gets its answer
    await fetch("https://h.example/heed/report", { method: "POST" });
    await new Promise((res) => setTimeout(res, 0));
    const net = r.snapshot({ console: false, network: true }).network!;
    expect(net).toHaveLength(1);
    expect(net[0]).toMatchObject({ method: "POST", url: "https://api.example/users/[id]?session=[redacted]", status: 404, ok: false });
    expect(JSON.stringify(net)).not.toContain("secret body");
  });

  it("passes a failing fetch's rejection through", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("offline"))));
    const r = start(window);
    r.enable({ console: false, network: true });
    await expect(fetch("/x")).rejects.toThrow("offline");
    await new Promise((res) => setTimeout(res, 0));
    expect(r.snapshot({ console: false, network: true }).network![0]).toMatchObject({ status: null, ok: false });
  });
});

describe("helpers", () => {
  it("masks spaced and nested secrets whole", () => {
    const r = start(window);
    r.enable({ console: true, network: false });
    console.log({ password: "correct horse battery staple" }, { auth: { token: "SECRETVALUE123" } });
    console.log("back from https://app.example/cb?code=4/0AbCdEf&state=xyz");
    const [one, two] = r.snapshot({ console: true, network: false }).console!.map((e) => e.message);
    expect(one).not.toMatch(/horse|staple|SECRETVALUE/);
    expect(two).toBe("back from https://app.example/cb?code=[redacted]&state=[redacted]");
  });

  it("never breaks or stalls the page's console call", () => {
    const r = start(window);
    r.enable({ console: true, network: false });
    const { proxy, revoke } = Proxy.revocable({}, {});
    revoke();
    const odd = new Error("x");
    Object.defineProperty(odd, "stack", { value: 42 });
    let reads = 0;
    const lazy = {
      get heavy() {
        reads++;
        console.log("inside a getter");
        return 1;
      },
    };
    const start_ = performance.now();
    expect(() => console.log(proxy, odd, new Uint8Array(5_000_000), lazy)).not.toThrow();
    expect(performance.now() - start_).toBeLessThan(200);
    expect(reads).toBe(0); // accessors are never called
    const last = r.snapshot({ console: true, network: false }).console!.at(-1)!.message;
    expect(last).toContain("[Uint8Array(5000000)]");
    expect(last).toContain('"heavy": [getter]');
  });

  it("describes arguments within bounds", () => {
    expect(describeArg({ a: [1, "x"], b: { c: { d: { e: 1 } } } })).toBe('{"a": [1, "x"], "b": {"c": {"d": {…}}}}');
    const cyclic: Record<string, unknown> = {};
    cyclic.self = cyclic;
    expect(describeArg(cyclic).length).toBeLessThan(100);
    expect(describeArg(document.body)).toBe("<body>");
  });

  it("masks before cutting, and never leaves half a secret", () => {
    const msg = maskMessage("x ".repeat(4000) + "token=abcdef", 2000);
    expect(msg.length).toBeLessThanOrEqual(2000);
    expect(maskMessage("token=abc", 2000)).toBe("token=[redacted]");
  });

  it("fits entries into the field cap, newest kept", () => {
    const entries = Array.from({ length: 10 }, (_, i) => ({ level: "log", message: `m${i}`.padEnd(20, ".") }));
    const one = JSON.stringify(entries[0]).length;
    const fitted = JSON.parse(fitJson(entries, 2 + 3 * (one + 1)));
    expect(fitted.map((e: { message: string }) => e.message.slice(0, 2))).toEqual(["m7", "m8", "m9"]);
    expect(JSON.parse(fitJson(entries, 1_000_000))).toHaveLength(10);
    expect(fitJson(entries, 1)).toBe("[]");
  });
});
