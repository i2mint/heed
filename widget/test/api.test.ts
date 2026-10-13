import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installApi, type HeedApi } from "../src/api";
import { TAG, defineHeedElement, type HeedFeedbackElement } from "../src/element";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const w = window as any;
const managed = () => document.querySelector(`${TAG}[data-heed-managed]`) as HeedFeedbackElement | null;
const boot = (data: Record<string, string> = {}) => ["_boot", { base: "https://h.example/heed/widget/heed.js", data }];

beforeEach(() => {
  defineHeedElement();
  delete w.heed;
});
afterEach(() => {
  w.heed?.destroy?.(); // stop this test's API from re-attaching its widget
  document.body.replaceChildren();
  delete w.heed;
});

describe("window.heed", () => {
  it("replays the loader's boot then the page's calls, in order", () => {
    w.heed = { q: [boot({ label: "Bug?", "page-url": "full" }), ["init", { position: "bottom-left" }]] };
    const api = installApi(w);
    const el = managed()!;
    expect(el.options.endpoint).toBe("https://h.example/heed/report");
    expect(el.options.label).toBe("Bug?");
    expect(el.options.pageUrl).toBe("full");
    expect(el.options.position).toBe("bottom-left");
    expect(api.loaded).toBe(true);
    expect(w.heed).toBe(api);
  });

  it("accepts an inline stub's arguments objects", () => {
    function stub(..._: unknown[]) {
      // eslint-disable-next-line prefer-rest-params
      ((stub as unknown as { q: unknown[] }).q ??= []).push(arguments);
    }
    w.heed = stub;
    stub("init", { endpoint: "/r", label: "Hi" });
    installApi(w);
    expect(managed()!.options.label).toBe("Hi");
  });

  it("does not mount with data-auto=false until asked", () => {
    w.heed = { q: [boot({ auto: "false", label: "Later" })] };
    const api = installApi(w) as HeedApi;
    expect(managed()).toBeNull();
    api.open();
    expect(managed()!.options.label).toBe("Later");
    expect(managed()!.shadowRoot!.querySelector("dialog")!.open).toBe(true);
  });

  it("opens an element the page wrote itself rather than adding one", () => {
    const own = document.createElement(TAG) as HeedFeedbackElement;
    own.setAttribute("endpoint", "/r");
    document.body.append(own);
    const api = installApi(w);
    api.open();
    expect(document.querySelectorAll(TAG)).toHaveLength(1);
    expect(own.shadowRoot!.querySelector("dialog")!.open).toBe(true);
  });

  it("on() listens to widget events and unsubscribes", () => {
    const api = installApi(w);
    const el = api.init({ endpoint: "/r" })!;
    const seen = vi.fn();
    const off = api.on("submitted", seen);
    el.dispatchEvent(new CustomEvent("heed:submitted", { detail: { id: "1" }, bubbles: true, composed: true }));
    off();
    el.dispatchEvent(new CustomEvent("heed:submitted", { detail: { id: "2" }, bubbles: true, composed: true }));
    expect(seen).toHaveBeenCalledTimes(1);
    expect(seen.mock.calls[0][0]).toEqual({ id: "1" });
  });

  it("init reconfigures the same element; destroy removes it", () => {
    const api = installApi(w);
    const a = api.init({ endpoint: "/r" });
    const b = api.init({ label: "Again" });
    expect(a).toBe(b);
    expect(b!.options.endpoint).toBe("/r");
    api.destroy();
    expect(managed()).toBeNull();
  });

  it("warns on unknown commands and survives a failing one", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    w.heed = { q: [["frobnicate"]] };
    const api = installApi(w);
    expect(warn).toHaveBeenCalledWith(expect.stringContaining("frobnicate"));
    expect(api("open")).toBeUndefined();
    warn.mockRestore();
  });

  it("is installed once", () => {
    const api = installApi(w);
    expect(installApi(w)).toBe(api);
  });
  it("ready() calls back with the API", () => {
    const seen = vi.fn();
    w.heed = { q: [["ready", seen]] };
    const api = installApi(w);
    expect(seen).toHaveBeenCalledWith(api);
  });

  it("puts the widget back when the page swaps <body>", async () => {
    const api = installApi(w);
    api.init({ endpoint: "/r" });
    document.body.replaceWith(document.createElement("body"));
    await new Promise((r) => setTimeout(r, 0));
    expect(managed()?.isConnected).toBe(true);
  });

  it("does not add a second widget beside one the page wrote, but lends it the endpoint", () => {
    const own = document.createElement(TAG) as HeedFeedbackElement;
    document.body.append(own);
    w.heed = { q: [boot()] };
    installApi(w);
    expect(document.querySelectorAll(TAG)).toHaveLength(1);
    expect(own.options.endpoint).toBe("https://h.example/heed/report");
  });

  it.each(["false", "0", "no", "False"])("data-auto=%s does not mount", (value) => {
    w.heed = { q: [boot({ auto: value })] };
    installApi(w);
    expect(managed()).toBeNull();
  });

  it("hands queued on() entries their real unsubscribe", () => {
    const entry: unknown[] & { off?: unknown } = ["on", "open", () => {}];
    w.heed = { q: [entry] };
    installApi(w);
    expect(typeof entry.off).toBe("function");
  });
  it("puts the widget back when the page swaps the body's contents (htmx boost)", async () => {
    const api = installApi(w);
    api.init({ endpoint: "/r" });
    await new Promise((r) => setTimeout(r, 0));
    document.body.replaceChildren(document.createElement("main"));
    await new Promise((r) => setTimeout(r, 0));
    expect(managed()?.isConnected).toBe(true);
  });

  it("drops the dead copy a restored body snapshot carries", async () => {
    const api = installApi(w);
    api.init({ endpoint: "/r" });
    await new Promise((r) => setTimeout(r, 0));
    document.body.replaceWith(document.body.cloneNode(true));
    await new Promise((r) => setTimeout(r, 0));
    expect(document.querySelectorAll(TAG)).toHaveLength(1);
    expect(managed()!.options.endpoint).toBe("/r");
  });

  it("an unsubscribe handed out by the stub removes the real listener after load", () => {
    const seen = vi.fn();
    const entry: unknown[] & { off?: () => void } = ["on", "open", seen];
    const q = [entry];
    w.heed = { q };
    const api = installApi(w);
    expect(q).toHaveLength(0);
    entry.off!();
    api.open();
    expect(seen).not.toHaveBeenCalled();
  });

  it("ignores an element named heed", () => {
    const el = document.createElement("div");
    el.id = "heed";
    document.body.append(el);
    const api = installApi(w);
    expect(api.loaded).toBe(true);
  });
});
