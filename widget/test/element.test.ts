import { afterEach, describe, expect, it, vi } from "vitest";
import "../src/index";
import { HeedFeedbackElement, TAG, defineHeedElement } from "../src/element";
import { recorded, setRecorderLoader, stopRecording } from "../src/record";

// Screenshots are covered in screenshot.test.ts; off here so sends start at once.
const mount = (attrs: Record<string, string> = { endpoint: "https://h.example/heed/report" }) => {
  const el = document.createElement(TAG) as HeedFeedbackElement;
  el.setAttribute("screenshot", "false");
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  document.body.append(el);
  return el;
};
const $ = <T extends Element>(el: HeedFeedbackElement, sel: string) => el.shadowRoot!.querySelector(sel) as T;
const fill = (el: HeedFeedbackElement, title: string, body = "") => {
  $<HTMLInputElement>(el, "input[name=title]").value = title;
  $<HTMLTextAreaElement>(el, "textarea").value = body;
};
const submit = async (el: HeedFeedbackElement) => {
  $<HTMLFormElement>(el, "form").requestSubmit();
  await vi.waitFor(() => expect($<HTMLButtonElement>(el, "button[type=submit]").disabled).toBe(false));
  await new Promise((r) => setTimeout(r, 0));
};

afterEach(() => {
  document.body.replaceChildren();
  vi.unstubAllGlobals();
});

describe("<heed-feedback>", () => {
  it("is registered once, with an open shadow root holding a launcher and a dialog", () => {
    defineHeedElement(); // a second load is harmless
    const el = mount();
    expect(customElements.get(TAG)).toBe(HeedFeedbackElement);
    expect(el.shadowRoot?.mode).toBe("open");
    expect($(el, "button.launcher")?.textContent).toBe("Feedback");
    expect($(el, "dialog")).toBeInstanceOf(HTMLDialogElement);
    expect(el.shadowRoot!.innerHTML).not.toContain("<script");
  });

  it("follows its attributes", () => {
    const el = mount({ endpoint: "/r", label: "Report a bug", position: "bottom-left", categories: "bug" });
    expect($(el, ".launcher").textContent).toBe("Report a bug");
    expect($(el, ".launcher").classList).toContain("bottom-left");
    expect(($(el, "select") as HTMLSelectElement).options).toHaveLength(1);
    expect(($(el, "select").parentElement as HTMLElement).hidden).toBe(true);
    el.setAttribute("position", "none");
    expect(($(el, ".launcher") as HTMLElement).hidden).toBe(true);
  });

  it("opens from the launcher and closes keeping the draft", () => {
    const el = mount();
    const events: string[] = [];
    el.addEventListener("heed:open", () => events.push("open"));
    el.addEventListener("heed:close", () => events.push("close"));
    ($(el, ".launcher") as HTMLButtonElement).click();
    expect($<HTMLDialogElement>(el, "dialog").open).toBe(true);
    fill(el, "draft title");
    el.close();
    expect($<HTMLDialogElement>(el, "dialog").open).toBe(false);
    el.open();
    expect($<HTMLInputElement>(el, "input[name=title]").value).toBe("draft title");
    expect(events).toEqual(["open", "close", "open"]);
  });

  it("refuses a blank title without sending", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const el = mount();
    el.open();
    fill(el, "   ");
    $<HTMLFormElement>(el, "form").requestSubmit();
    await new Promise((r) => setTimeout(r, 0));
    expect(fetch).not.toHaveBeenCalled();
    expect($(el, ".error").textContent).toMatch(/title/);
  });

  it("sends a report and shows the thank-you view", async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify({ id: "abc", ok: true, ref: "7", url: "https://github.com/o/r/issues/7", status: "received" }), { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    const el = mount();
    el.configure({ extra: () => ({ build: "42" }) });
    const submitted = vi.fn();
    el.addEventListener("heed:submitted", (e) => submitted((e as CustomEvent).detail));
    el.open();
    fill(el, "Save fails", "It spins forever");
    await submit(el);
    const body = (fetch.mock.calls[0] as unknown as [string, RequestInit])[1].body as FormData;
    expect(body.get("title")).toBe("Save fails");
    expect(JSON.parse(body.get("env") as string).extra).toEqual({ build: "42" });
    expect(body.get("page_url")).toBe(location.origin + location.pathname);
    expect(submitted).toHaveBeenCalledWith({ id: "abc", status: "received", url: "https://github.com/o/r/issues/7" });
    expect(($(el, ".done") as HTMLElement).hidden).toBe(false);
    expect($<HTMLAnchorElement>(el, ".done a").href).toBe("https://github.com/o/r/issues/7");
    expect($<HTMLInputElement>(el, "input[name=title]").value).toBe("");
  });

  it("keeps what was typed when sending fails, and says why", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "rate limit exceeded" }), { status: 429, headers: { "Retry-After": "60" } })));
    const el = mount();
    const errors = vi.fn();
    el.addEventListener("heed:error", (e) => errors((e as CustomEvent).detail));
    el.open();
    fill(el, "Keep me", "and me");
    await submit(el);
    expect($(el, ".error").textContent).toMatch(/60 seconds/);
    expect($(el, ".error").getAttribute("role")).toBe("alert");
    expect($<HTMLInputElement>(el, "input[name=title]").value).toBe("Keep me");
    expect($<HTMLTextAreaElement>(el, "textarea").value).toBe("and me");
    expect(errors).toHaveBeenCalledWith({ kind: "rate_limited" });
  });

  it("sends the honeypot's content, so a bot that fills it is dropped server-side", async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify({ id: "x", ok: true, status: "received" })));
    vi.stubGlobal("fetch", fetch);
    const el = mount();
    el.open();
    fill(el, "spam");
    $<HTMLInputElement>(el, ".hp input").value = "http://spam.example";
    await submit(el);
    const body = (fetch.mock.calls[0] as unknown as [string, RequestInit])[1].body as FormData;
    expect(body.get("hp_website")).toBe("http://spam.example");
    expect($(el, ".hp").getAttribute("aria-hidden")).toBe("true");
  });

  it("ignores a second submit while one is in flight", async () => {
    let release!: (r: Response) => void;
    const fetch = vi.fn(() => new Promise<Response>((r) => (release = r)));
    vi.stubGlobal("fetch", fetch);
    const el = mount();
    el.open();
    fill(el, "once");
    $<HTMLFormElement>(el, "form").requestSubmit();
    $<HTMLFormElement>(el, "form").requestSubmit();
    await new Promise((r) => setTimeout(r, 0));
    expect(fetch).toHaveBeenCalledTimes(1);
    release(new Response(JSON.stringify({ id: "x", ok: true, status: "received" })));
  });

  it("says so when no endpoint is configured", async () => {
    const el = mount({});
    el.open();
    fill(el, "t");
    $<HTMLFormElement>(el, "form").requestSubmit();
    await new Promise((r) => setTimeout(r, 0));
    expect($(el, ".error").textContent).toMatch(/not set up/);
  });
  it("keeps botToken/extra set before the element was upgraded", async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify({ id: "x", ok: true, status: "received" })));
    vi.stubGlobal("fetch", fetch);
    const early = document.createElement("heed-early") as HeedFeedbackElement;
    early.setAttribute("endpoint", "/r");
    early.setAttribute("screenshot", "false");
    (early as unknown as { botToken: unknown }).botToken = () => "early-token";
    document.body.append(early);
    customElements.define("heed-early", class extends HeedFeedbackElement {});
    early.open();
    fill(early, "t");
    await submit(early);
    const body = (fetch.mock.calls[0] as unknown as [string, RequestInit])[1].body as FormData;
    expect(body.get("bot_token")).toBe("early-token");
  });

  it("warns instead of throwing when opened outside the document", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const el = document.createElement(TAG) as HeedFeedbackElement;
    expect(() => el.open()).not.toThrow();
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });

  it("closes its dialog when removed from the document", () => {
    const el = mount();
    el.open();
    el.remove();
    expect($<HTMLDialogElement>(el, "dialog").open).toBe(false);
  });
});

describe("<heed-feedback> opt-in capture (heed#7)", () => {
  const ok = () => vi.fn(async () => new Response(JSON.stringify({ id: "abc", ok: true, ref: null, url: null, status: "received" }), { status: 200 }));
  const sent = (fetch: ReturnType<typeof ok>) => (fetch.mock.calls.at(-1) as unknown as [string, RequestInit])[1].body as FormData;

  afterEach(() => stopRecording(window));

  it("offers no consent box and sends no capture by default", async () => {
    const fetch = ok();
    vi.stubGlobal("fetch", fetch);
    const el = mount();
    el.open();
    expect(($(el, ".logs") as HTMLElement).hidden).toBe(true);
    fill(el, "t");
    await submit(el);
    expect(sent(fetch).has("console")).toBe(false);
    expect(sent(fetch).has("network")).toBe(false);
  });

  it("attaches masked capture only when the visitor ticks the unticked box", async () => {
    setRecorderLoader(async () => import("../src/recorder"));
    const fetch = ok();
    vi.stubGlobal("fetch", fetch);
    const el = mount({ endpoint: "https://h.example/heed/report", "capture-console": "", "capture-network": "" });
    await vi.waitFor(() => {
      console.error("failed for ana@example.com");
      expect(recorded(window, { console: true, network: false }).console?.length).toBeGreaterThan(0);
    });
    el.open();
    const box = $<HTMLInputElement>(el, ".logs input");
    expect(($(el, ".logs") as HTMLElement).hidden).toBe(false);
    expect(box.checked).toBe(false);
    fill(el, "first");
    await submit(el);
    expect(sent(fetch).has("console")).toBe(false);

    el.open();
    fill(el, "second");
    box.checked = true;
    await submit(el);
    const logs = JSON.parse(sent(fetch).get("console") as string);
    expect(logs.at(-1)).toMatchObject({ level: "error", message: "failed for [email]" });
    expect(sent(fetch).get("network")).toBeNull(); // heed's own POST is never recorded
    expect(box.checked).toBe(false); // consent is per report
  });

  it("hides the box for light categories and sends nothing then", async () => {
    setRecorderLoader(async () => import("../src/recorder"));
    const fetch = ok();
    vi.stubGlobal("fetch", fetch);
    const el = mount({ endpoint: "/r", "capture-console": "" });
    el.open();
    const select = $<HTMLSelectElement>(el, "select");
    select.value = "feature";
    select.dispatchEvent(new Event("change"));
    expect(($(el, ".logs") as HTMLElement).hidden).toBe(true);
    $<HTMLInputElement>(el, ".logs input").checked = true;
    fill(el, "idea");
    await submit(el);
    expect(sent(fetch).has("console")).toBe(false);
  });
});
