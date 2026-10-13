import { afterEach, describe, expect, it, vi } from "vitest";
import "../src/index";
import { HeedFeedbackElement, TAG } from "../src/element";
import { MAX_SCREENSHOT_BYTES, captureWithin } from "../src/capture";
import { CONTRACT } from "../src/contract.gen";

const png = () => new Blob([new Uint8Array([137, 80, 78, 71])], { type: "image/png" });
const tick = (ms = 0) => new Promise((r) => setTimeout(r, ms));
const $ = <T extends Element>(el: HeedFeedbackElement, sel: string) => el.shadowRoot!.querySelector(sel) as T;
const ok = () => vi.fn(async () => new Response(JSON.stringify({ id: "x", ok: true, status: "received" })));
const sentBody = (fetch: ReturnType<typeof ok>) => (fetch.mock.calls[0] as unknown as [string, RequestInit])[1].body as FormData;

function mount(take: unknown, attrs: Record<string, string> = {}) {
  const el = document.createElement(TAG) as HeedFeedbackElement;
  el.setAttribute("endpoint", "/r");
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  el.configure({ takeScreenshot: take });
  document.body.append(el);
  return el;
}
/** jsdom loads no images: play the browser's part once the preview has a src. */
async function previewShown(el: HeedFeedbackElement, event: "load" | "error" = "load") {
  await vi.waitFor(() => expect($<HTMLImageElement>(el, ".preview").getAttribute("src")).toBeTruthy());
  $(el, ".preview").dispatchEvent(new Event(event));
  await tick();
}
async function send(el: HeedFeedbackElement, title = "t") {
  $<HTMLInputElement>(el, "input[name=title]").value = title;
  $<HTMLFormElement>(el, "form").requestSubmit();
  await vi.waitFor(() => expect($<HTMLButtonElement>(el, "button[type=submit]").disabled).toBe(false));
  await tick();
}

afterEach(() => {
  document.body.replaceChildren();
  vi.unstubAllGlobals();
});

describe("screenshot", () => {
  it("is taken when the dialog opens, previewed, and sent", async () => {
    const fetch = ok();
    vi.stubGlobal("fetch", fetch);
    const take = vi.fn(async () => png());
    const el = mount(take);
    el.open();
    expect($(el, ".shot").textContent).toMatch(/Taking a screenshot/);
    await previewShown(el);
    expect(take).toHaveBeenCalledWith(window, {
      maskInputs: true,
      maskSelector: "[data-heed-mask]",
      maxNodes: 5000,
      maxBytes: MAX_SCREENSHOT_BYTES,
      maxPixels: CONTRACT.limits.screenshot_pixels,
    });
    expect(MAX_SCREENSHOT_BYTES).toBeLessThan(CONTRACT.limits.screenshot_bytes);
    await send(el);
    const file = sentBody(fetch).get("screenshot") as File;
    expect(file.name).toBe("screenshot.png");
    expect(file.size).toBe(4);
  });

  it("is not sent when the visitor unticks it", async () => {
    const fetch = ok();
    vi.stubGlobal("fetch", fetch);
    const el = mount(async () => png());
    el.open();
    await previewShown(el);
    $<HTMLInputElement>(el, ".shot input").checked = false;
    await send(el);
    expect(sentBody(fetch).has("screenshot")).toBe(false);
  });

  it("never sends a screenshot the visitor has not seen: a Send during capture asks to review", async () => {
    const fetch = ok();
    vi.stubGlobal("fetch", fetch);
    const el = mount(() => tick(80).then(png));
    el.open();
    $<HTMLInputElement>(el, "input[name=title]").value = "quick";
    $<HTMLFormElement>(el, "form").requestSubmit();
    await previewShown(el);
    await vi.waitFor(() => expect($(el, ".status").textContent).toMatch(/Check the screenshot/));
    expect(fetch).not.toHaveBeenCalled();
    await send(el, "quick");
    expect(sentBody(fetch).has("screenshot")).toBe(true);
  });

  it("is not sent when its preview cannot be shown", async () => {
    const fetch = ok();
    vi.stubGlobal("fetch", fetch);
    const el = mount(async () => png());
    el.open();
    await previewShown(el, "error");
    expect($(el, ".shot").textContent).toMatch(/will not be sent/);
    expect($<HTMLInputElement>(el, ".shot input").checked).toBe(false);
    await send(el);
    expect(sentBody(fetch).has("screenshot")).toBe(false);
  });

  it("retries without the screenshot when the server says it is too large", async () => {
    const fetch = vi.fn(async (_u: string, init: RequestInit) =>
      (init.body as FormData).has("screenshot")
        ? new Response(JSON.stringify({ detail: "screenshot too large" }), { status: 413 })
        : new Response(JSON.stringify({ id: "x", ok: true, status: "received" })),
    );
    vi.stubGlobal("fetch", fetch);
    const el = mount(async () => png());
    el.open();
    await previewShown(el);
    await send(el);
    expect(fetch).toHaveBeenCalledTimes(2);
    expect($(el, ".done").textContent).toMatch(/too large and was left out/);
  });

  it("sends without a screenshot when capture fails or takes too long", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    for (const take of [async () => { throw new Error("tainted"); }, () => new Promise<Blob>(() => {})]) {
      const fetch = ok();
      vi.stubGlobal("fetch", fetch);
      const el = mount(take, { "screenshot-timeout-ms": "100" });
      el.open();
      await vi.waitFor(() => expect($(el, ".shot").textContent).toMatch(/No screenshot could be taken/), { timeout: 2000 });
      expect($<HTMLInputElement>(el, ".shot input").disabled).toBe(true);
      await send(el);
      expect(sentBody(fetch).has("screenshot")).toBe(false);
      el.remove();
    }
    warn.mockRestore();
  });

  it("is off with screenshot=false", async () => {
    const take = vi.fn(async () => png());
    const el = mount(take, { screenshot: "false" });
    el.open();
    await tick(80);
    expect(take).not.toHaveBeenCalled();
    expect(($(el, ".shot") as HTMLElement).hidden).toBe(true);
  });

  it("passes the masking options through", async () => {
    const take = vi.fn(async () => null);
    const el = mount(take, { "mask-inputs": "false", "mask-selector": ".secret", "screenshot-max-nodes": "99" });
    el.open();
    await vi.waitFor(() => expect(take).toHaveBeenCalled());
    expect((take.mock.calls[0] as unknown[])[1]).toMatchObject({ maskInputs: false, maskSelector: ".secret", maxNodes: 99 });
  });

  it("drops an oversized or empty result", async () => {
    const opts = { maskInputs: true, maskSelector: "", maxNodes: 5000 };
    const big = new Blob([new Uint8Array(MAX_SCREENSHOT_BYTES + 1)], { type: "image/png" });
    expect(await captureWithin(async () => big, window, opts, 1000)).toBeNull();
    expect(await captureWithin(async () => new Blob([]), window, opts, 1000)).toBeNull();
  });

  it("re-captures on every open and resets after sending", async () => {
    vi.stubGlobal("fetch", ok());
    const take = vi.fn(async () => png());
    const el = mount(take);
    el.open();
    el.close();
    el.open();
    await vi.waitFor(() => expect(take).toHaveBeenCalledTimes(2));
    await previewShown(el);
    await send(el);
    expect($<HTMLImageElement>(el, ".preview").hidden).toBe(true);
  });
});
