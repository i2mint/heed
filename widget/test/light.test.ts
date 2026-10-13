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
    expect($<HTMLElement>(el, ".preview").hidden).toBe(true);
    expect($(el, ".note").textContent).toMatch(/Sent with your idea/);
    expect($(el, ".note").textContent).not.toBe(note);
    await send(el);
    const body = sent(fetch);
    expect(body.has("screenshot")).toBe(false);
    expect(body.get("category")).toBe("feature");
    expect(envOf(fetch)).toEqual({ extra: { version: "1.2.3" } });
  });

  it("takes the screenshot again when the visitor goes back to a bug", async () => {
    const fetch = ok();
    vi.stubGlobal("fetch", fetch);
    const take = vi.fn(async () => png());
    const el = mount(take);
    el.open();
    await previewShown(el);
    choose(el, "feature");
    expect(take).toHaveBeenCalledTimes(1);
    choose(el, "bug");
    await vi.waitFor(() => expect(take).toHaveBeenCalledTimes(2));
    await previewShown(el);
    await send(el);
    expect(sent(fetch).has("screenshot")).toBe(true);
    expect(envOf(fetch).user_agent).toBeTruthy();
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
    expect($(el, ".note").textContent).toMatch(/Sent with your idea/);
    el.setAttribute("categories", "bug"); // the selected category is now bug
    expect($(el, ".note").textContent).not.toMatch(/Sent with your idea/);
    await vi.waitFor(() => expect(take).toHaveBeenCalledTimes(1));
    await previewShown(el);
    await send(el);
    expect(sent(fetch).has("screenshot")).toBe(true);
    expect(envOf(fetch).user_agent).toBeTruthy();
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

  it("waits for the newer capture when Send was pressed during one the visitor replaced", async () => {
    const fetch = ok();
    vi.stubGlobal("fetch", fetch);
    const take = vi.fn(async () => {
      await tick(30);
      return png();
    });
    const el = mount(take);
    el.open();
    $<HTMLInputElement>(el, "input[name=title]").value = "t";
    $<HTMLFormElement>(el, "form").requestSubmit(); // waits for the first capture
    await tick(5);
    choose(el, "feature");
    choose(el, "bug"); // a second capture replaces the first
    await vi.waitFor(() => expect(take).toHaveBeenCalledTimes(2));
    await previewShown(el);
    await vi.waitFor(() => expect($(el, ".status").textContent).toMatch(/Check the screenshot/));
    expect(fetch).not.toHaveBeenCalled(); // not sent without the picture the visitor was shown
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
