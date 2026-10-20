import { afterEach, describe, expect, it, vi } from "vitest";
import "../src/index";
import { HeedFeedbackElement, TAG } from "../src/element";
import { collectEnvironment } from "../src/env";
import { defaultOptions, optionsFromAttributes } from "../src/options";

const png = () => new Blob([new Uint8Array([137, 80, 78, 71])], { type: "image/png" });
const tick = (ms = 0) => new Promise((r) => setTimeout(r, ms));
const $ = <T extends Element>(el: HeedFeedbackElement, sel: string) => el.shadowRoot!.querySelector(sel) as T;
const ok = () => vi.fn(async () => new Response(JSON.stringify({ id: "x", ok: true, status: "received" })));
const sent = (fetch: ReturnType<typeof ok>) => (fetch.mock.calls[0] as unknown as [string, RequestInit])[1].body as FormData;

function mount(take: ReturnType<typeof vi.fn>, attrs: Record<string, string> = {}) {
  const el = document.createElement(TAG) as HeedFeedbackElement;
  el.setAttribute("endpoint", "/r");
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  el.configure({ takeScreenshot: take, extra: () => ({ version: "1.2.3" }) });
  document.body.append(el);
  return el;
}
function choose(el: HeedFeedbackElement, category: string) {
  const select = $<HTMLSelectElement>(el, "select");
  select.value = category;
  select.dispatchEvent(new Event("change"));
}
async function previewShown(el: HeedFeedbackElement) {
  await vi.waitFor(() => expect($<HTMLImageElement>(el, ".preview").getAttribute("src")).toBeTruthy());
  $(el, ".preview").dispatchEvent(new Event("load"));
  await tick();
}
async function send(el: HeedFeedbackElement) {
  $<HTMLInputElement>(el, "input[name=title]").value = "an idea";
  $<HTMLFormElement>(el, "form").requestSubmit();
  await vi.waitFor(() => expect($<HTMLButtonElement>(el, "button[type=submit]").disabled).toBe(false));
  await tick();
}
const envOf = (fetch: ReturnType<typeof ok>) => JSON.parse(sent(fetch).get("env") as string);

afterEach(() => {
  document.body.replaceChildren();
  vi.unstubAllGlobals();
});

describe("light intake for feature requests", () => {
  it("is the default for feature and question, and can be switched off or changed", () => {
    expect(defaultOptions().lightCategories).toEqual(["feature", "question"]);
    expect(optionsFromAttributes((a) => (a === "light-categories" ? "idea, other,feature" : null)).lightCategories).toEqual(["other", "feature"]);
    expect(optionsFromAttributes((a) => (a === "light-categories" ? "" : null)).lightCategories).toEqual([]);
  });

  it("sends no browser details, only the embedder's extra facts", () => {
    expect(collectEnvironment(window, () => ({ version: "1" }), true)).toEqual({ extra: { version: "1" } });
    expect(collectEnvironment(window, null, false).user_agent).toBeTruthy();
  });

  it("drops the screenshot and the browser details once a feature request is chosen", async () => {
    const fetch = ok();
    vi.stubGlobal("fetch", fetch);
    const take = vi.fn(async () => png());
    const el = mount(take);
    el.open();
    await previewShown(el);
    expect($<HTMLElement>(el, ".shot").hidden).toBe(false);
    const note = $(el, ".note").textContent;
    choose(el, "feature");
    expect($<HTMLElement>(el, ".shot").hidden).toBe(true);
    expect($(el, ".note").textContent).toMatch(/Sent with your message/);
    expect($(el, ".note").textContent).not.toBe(note);
    await send(el);
    const body = sent(fetch);
    expect(body.has("screenshot")).toBe(false);
    expect(body.get("category")).toBe("feature");
    expect(envOf(fetch)).toEqual({ extra: { version: "1.2.3" } });
  });

  it("keeps the visitor's choice about the screenshot across a round trip through a feature request", async () => {
    const fetch = ok();
    vi.stubGlobal("fetch", fetch);
    const take = vi.fn(async () => png());
    const el = mount(take);
    el.open();
    await previewShown(el);
    $<HTMLInputElement>(el, ".shot input").checked = false; // the visitor opts out
    choose(el, "feature");
    choose(el, "bug");
    expect($<HTMLInputElement>(el, ".shot input").checked).toBe(false);
    expect(take).toHaveBeenCalledTimes(1); // no second capture
    await send(el);
    expect(sent(fetch).has("screenshot")).toBe(false);
    expect(envOf(fetch).user_agent).toBeTruthy(); // a bug again: full details
  });

  it("sends the same screenshot after a round trip, and starts no capture per switch", async () => {
    const fetch = ok();
    vi.stubGlobal("fetch", fetch);
    const take = vi.fn(async () => png());
    const el = mount(take);
    el.open();
    await previewShown(el);
    for (let i = 0; i < 4; i++) {
      choose(el, "feature");
      choose(el, "bug");
    }
    expect(take).toHaveBeenCalledTimes(1);
    await send(el);
    expect(sent(fetch).has("screenshot")).toBe(true);
  });

  it("never takes a screenshot when the dialog opens on a light category", async () => {
    const fetch = ok();
    vi.stubGlobal("fetch", fetch);
    const take = vi.fn(async () => png());
    const el = mount(take, { categories: "feature,bug" });
    el.open();
    await tick(120);
    expect(take).not.toHaveBeenCalled();
    await send(el);
    expect(sent(fetch).has("screenshot")).toBe(false);
  });

  it("keeps the full capture for every category when light-categories is empty", async () => {
    const fetch = ok();
    vi.stubGlobal("fetch", fetch);
    const take = vi.fn(async () => png());
    const el = mount(take, { "light-categories": "" });
    el.open();
    await previewShown(el);
    choose(el, "feature");
    expect($<HTMLElement>(el, ".preview").hidden).toBe(false);
    await send(el);
    expect(sent(fetch).has("screenshot")).toBe(true);
    expect(envOf(fetch).user_agent).toBeTruthy();
  });

  it("follows a changed `categories` attribute while the dialog is open", async () => {
    const fetch = ok();
    vi.stubGlobal("fetch", fetch);
    const take = vi.fn(async () => png());
    const el = mount(take, { categories: "feature bug" });
    el.open();
    expect($(el, ".note").textContent).toMatch(/Sent with your message/);
    el.setAttribute("categories", "bug"); // the selected category is now bug
    expect($(el, ".note").textContent).not.toMatch(/Sent with your message/);
    await vi.waitFor(() => expect(take).toHaveBeenCalledTimes(1));
    await previewShown(el);
    await send(el);
    expect(sent(fetch).has("screenshot")).toBe(true);
    expect(envOf(fetch).user_agent).toBeTruthy();
  });

  it("accepts an empty string for lightCategories in configure(), and announces the note", () => {
    const el = mount(vi.fn(async () => png()));
    el.configure({ lightCategories: "" });
    expect(el.options.lightCategories).toEqual([]);
    el.configure({ lightCategories: " bug " });
    expect(el.options.lightCategories).toEqual(["bug"]);
    expect(el.shadowRoot!.querySelector("p.note")!.getAttribute("role")).toBe("status");
  });

  it("takes the screenshot when light-categories is emptied while open on a feature", async () => {
    const fetch = ok();
    vi.stubGlobal("fetch", fetch);
    const take = vi.fn(async () => png());
    const el = mount(take, { categories: "feature bug" });
    el.open();
    expect(take).not.toHaveBeenCalled();
    el.configure({ lightCategories: [] });
    await vi.waitFor(() => expect(take).toHaveBeenCalledTimes(1));
    await previewShown(el);
    await send(el);
    expect(sent(fetch).has("screenshot")).toBe(true);
  });

  it("sends nothing but extra when a category turns light after its screenshot was shown", async () => {
    const fetch = ok();
    vi.stubGlobal("fetch", fetch);
    const el = mount(vi.fn(async () => png()));
    el.open();
    await previewShown(el);
    el.configure({ lightCategories: ["bug"] });
    await send(el);
    expect(sent(fetch).has("screenshot")).toBe(false);
    expect(envOf(fetch)).toEqual({ extra: { version: "1.2.3" } });
  });

  it("does not hang a send whose capture is cleared before its preview loads", async () => {
    const fetch = ok();
    vi.stubGlobal("fetch", fetch);
    const el = mount(vi.fn(async () => png()));
    el.open();
    await vi.waitFor(() => expect($<HTMLImageElement>(el, ".preview").getAttribute("src")).toBeTruthy());
    $<HTMLInputElement>(el, "input[name=title]").value = "t";
    $<HTMLFormElement>(el, "form").requestSubmit(); // waits for a preview that never loads
    await tick(5);
    expect($<HTMLButtonElement>(el, "button[type=submit]").disabled).toBe(true);
    el.close();
    el.open(); // a fresh capture replaces (and releases) the old one
    await vi.waitFor(() => expect($<HTMLImageElement>(el, ".preview").getAttribute("src")).toBeTruthy());
    $(el, ".preview").dispatchEvent(new Event("load")); // the new preview, which the send then waits for
    await vi.waitFor(() => expect($<HTMLButtonElement>(el, "button[type=submit]").disabled).toBe(false));
  });

  it("starts no capture behind the thank-you screen", async () => {
    const fetch = ok();
    vi.stubGlobal("fetch", fetch);
    const take = vi.fn(async () => png());
    const el = mount(take, { screenshot: "true" });
    el.open();
    await previewShown(el);
    await send(el);
    expect($<HTMLElement>(el, ".done").hidden).toBe(false);
    el.setAttribute("lang", "es"); // anything that syncs
    await tick(80);
    expect(take).toHaveBeenCalledTimes(1);
  });

  it("fixes the category while a report is on its way", async () => {
    let release: (r: Response) => void = () => {};
    const fetch = vi.fn(() => new Promise<Response>((r) => (release = r)));
    vi.stubGlobal("fetch", fetch);
    const el = mount(vi.fn(async () => png()), { screenshot: "false" });
    el.open();
    $<HTMLInputElement>(el, "input[name=title]").value = "t";
    $<HTMLFormElement>(el, "form").requestSubmit();
    await vi.waitFor(() => expect(fetch).toHaveBeenCalled());
    expect($<HTMLSelectElement>(el, "select").disabled).toBe(true);
    release(new Response(JSON.stringify({ id: "x", ok: true, status: "received" })));
    await vi.waitFor(() => expect($<HTMLSelectElement>(el, "select").disabled).toBe(false));
  });
});

describe("light intake: one decision per send", () => {
  it("sends a coherent report when the category changes from code while the screenshot is awaited", async () => {
    const fetch = ok();
    vi.stubGlobal("fetch", fetch);
    const take = vi.fn(async () => {
      await tick(20);
      return png();
    });
    const el = mount(take);
    el.open();
    $<HTMLInputElement>(el, "input[name=title]").value = "t";
    $<HTMLFormElement>(el, "form").requestSubmit(); // awaiting the capture (bug)
    await tick(5);
    el.configure({ categories: ["feature"] });
    await previewShown(el);
    await vi.waitFor(() => expect($<HTMLButtonElement>(el, "button[type=submit]").disabled).toBe(false));
    expect(fetch).not.toHaveBeenCalled(); // asked to review the picture first
    $<HTMLFormElement>(el, "form").requestSubmit();
    await vi.waitFor(() => expect(fetch).toHaveBeenCalled());
    const body = sent(fetch);
    const isLight = body.get("category") === "feature";
    expect(body.has("screenshot")).toBe(!isLight);
    expect(!!envOf(fetch).user_agent).toBe(!isLight);
  });

  it("takes a fresh screenshot of the page when a dialog closed on a feature request is reopened as a bug", async () => {
    const take = vi.fn(async () => png());
    const el = mount(take);
    el.open();
    await previewShown(el);
    choose(el, "feature");
    el.close();
    el.open(); // still a feature: no capture, and the old one is gone
    expect(take).toHaveBeenCalledTimes(1);
    choose(el, "bug");
    await vi.waitFor(() => expect(take).toHaveBeenCalledTimes(2));
  });

  it("words the note after the page-url mode and what the embedder adds", () => {
    const el = mount(vi.fn(async () => png()), { categories: "feature" });
    el.open();
    const note = () => $(el, ".note").textContent!;
    expect(note()).toMatch(/\(without its query\)\. .*anonymous ID\.$/); // extra is configured
    el.setAttribute("page-url", "full");
    expect(note()).not.toMatch(/without its query/);
    expect(note()).toMatch(/^Sent with your message: this page's address\. /);
    el.configure({ extra: null });
    expect(note()).not.toMatch(/anonymous ID/);
    el.setAttribute("anon-id", "visitor_42");
    expect(note()).toMatch(/anonymous ID\.$/);
  });
});
