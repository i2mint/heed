import { afterEach, describe, expect, it, vi } from "vitest";
import "../src/index";
import { HeedFeedbackElement, TAG } from "../src/element";
import { GATES, createBotGate } from "../src/botgate";

type Cbs = { callback(t: string): void; "error-callback"(c: string): unknown; "timeout-callback"(): void };

/** A stand-in for Cloudflare's `window.turnstile`: records calls, answers `execute` with a token. */
function fakeTurnstile(answer: (cbs: Cbs, n: number) => void = (cbs, n) => cbs.callback(`tok${n}`)) {
  const calls: string[] = [];
  let cbs!: Cbs;
  let runs = 0;
  const api = {
    render: vi.fn((el: HTMLElement, params: Record<string, unknown>) => {
      calls.push("render");
      cbs = params as unknown as Cbs;
      el.append(document.createElement("iframe"));
      return "w1";
    }),
    execute: vi.fn(() => (calls.push("execute"), queueMicrotask(() => answer(cbs, ++runs)))),
    reset: vi.fn(() => calls.push("reset")),
    remove: vi.fn(() => calls.push("remove")),
  };
  return { api, calls, params: () => api.render.mock.calls[0][1] };
}

const loadOk = vi.fn(async () => {});
afterEach(() => {
  delete (window as unknown as Record<string, unknown>).turnstile;
  document.body.replaceChildren();
  vi.restoreAllMocks();
  loadOk.mockClear();
});

describe("createBotGate", () => {
  it("renders on warm, runs the challenge per token, and resets between runs", async () => {
    const fake = fakeTurnstile();
    (window as unknown as Record<string, unknown>).turnstile = fake.api;
    const box = document.createElement("div");
    const gate = createBotGate(window, box, { provider: "turnstile", sitekey: "SITE", load: loadOk })!;
    expect(fake.api.render).not.toHaveBeenCalled(); // nothing before it is asked
    await gate.warm();
    expect(fake.params()).toMatchObject({ sitekey: "SITE", execution: "execute", appearance: "interaction-only" });
    expect(await gate.token()).toBe("tok1");
    expect(await gate.token()).toBe("tok2");
    expect(fake.calls).toEqual(["render", "execute", "reset", "execute"]);
    expect(loadOk).toHaveBeenCalledWith(window, GATES.turnstile.script);
    gate.destroy();
    expect(fake.calls.at(-1)).toBe("remove");
    expect(box.children).toHaveLength(0);
  });

  it("loads the script once however often it is warmed", async () => {
    (window as unknown as Record<string, unknown>).turnstile = fakeTurnstile().api;
    const gate = createBotGate(window, document.createElement("div"), { provider: "turnstile", sitekey: "S", load: loadOk })!;
    await Promise.all([gate.warm(), gate.warm(), gate.token()]);
    expect(loadOk).toHaveBeenCalledTimes(1);
  });

  it("rejects when the provider reports an error, and when the script cannot load", async () => {
    (window as unknown as Record<string, unknown>).turnstile = fakeTurnstile((cbs) => cbs["error-callback"]("300030")).api;
    const gate = createBotGate(window, document.createElement("div"), { provider: "turnstile", sitekey: "S", load: loadOk })!;
    await expect(gate.token()).rejects.toThrow("300030");
    const broken = createBotGate(window, document.createElement("div"), {
      provider: "turnstile",
      sitekey: "S",
      load: async () => {
        throw new Error("blocked");
      },
    })!;
    await expect(broken.token()).rejects.toThrow("blocked");
    await expect(broken.token()).rejects.toThrow("blocked"); // not cached: a later send retries
  });

  it("rejects a token in flight when destroyed", async () => {
    (window as unknown as Record<string, unknown>).turnstile = fakeTurnstile(() => {}).api; // never answers
    const gate = createBotGate(window, document.createElement("div"), { provider: "turnstile", sitekey: "S", load: loadOk })!;
    const t = gate.token();
    await gate.warm();
    gate.destroy();
    await expect(t).rejects.toThrow("removed");
  });

  it("cancel() rejects the token being waited for, and says whether there was one", async () => {
    (window as unknown as Record<string, unknown>).turnstile = fakeTurnstile(() => {}).api;
    const gate = createBotGate(window, document.createElement("div"), { provider: "turnstile", sitekey: "S", load: loadOk })!;
    expect(gate.cancel()).toBe(false);
    const t = gate.token();
    await gate.warm();
    expect(gate.cancel()).toBe(true);
    await expect(t).rejects.toThrow("cancelled");
    expect(gate.cancel()).toBe(false);
  });

  it("shows its box only while the challenge needs the visitor", async () => {
    const fake = fakeTurnstile(() => {});
    (window as unknown as Record<string, unknown>).turnstile = fake.api;
    const box = document.createElement("div");
    const gate = createBotGate(window, box, { provider: "turnstile", sitekey: "S", load: loadOk })!;
    expect(box.hidden).toBe(true); // a hidden input inside would otherwise leave a gap in the form
    await gate.warm();
    const params = fake.params() as Record<string, () => void>;
    params["before-interactive-callback"]();
    expect(box.hidden).toBe(false);
    params["after-interactive-callback"]();
    expect(box.hidden).toBe(true);
  });

  it("drives hCaptcha: invisible, executed with async, reset between runs", async () => {
    const calls: string[] = [];
    let answers = 0;
    const api = {
      render: vi.fn((_el: HTMLElement, p: Record<string, unknown>) => (calls.push(`render:${p.size}`), "h1")),
      execute: vi.fn(async (_id: string, o: { async: boolean }) => (calls.push(`execute:${o.async}`), { response: `h${++answers}` })),
      reset: vi.fn(() => calls.push("reset")),
      remove: vi.fn(() => calls.push("remove")),
    };
    (window as unknown as Record<string, unknown>).hcaptcha = api;
    const gate = createBotGate(window, document.createElement("div"), { provider: "hcaptcha", sitekey: "S", load: loadOk })!;
    expect(await gate.token()).toBe("h1");
    expect(await gate.token()).toBe("h2");
    expect(calls).toEqual(["render:invisible", "execute:true", "reset", "execute:true"]);
    expect(loadOk).toHaveBeenCalledWith(window, GATES.hcaptcha.script);
    gate.destroy();
    delete (window as unknown as Record<string, unknown>).hcaptcha;
  });

  it("puts hCaptcha's challenge inside the dialog's box and shows the box while it is open", async () => {
    const seen: Record<string, unknown>[] = [];
    (window as unknown as Record<string, unknown>).hcaptcha = {
      render: (_el: HTMLElement, p: Record<string, unknown>) => (seen.push(p), "h1"),
      execute: () => new Promise(() => {}),
      reset() {},
      remove() {},
    };
    const box = document.createElement("div");
    const gate = createBotGate(window, box, { provider: "hcaptcha", sitekey: "S", load: loadOk })!;
    await gate.warm();
    expect(seen[0]["challenge-container"]).toBe(box);
    (seen[0]["open-callback"] as () => void)();
    expect(box.hidden).toBe(false);
    (seen[0]["close-callback"] as () => void)();
    expect(box.hidden).toBe(true);
    delete (window as unknown as Record<string, unknown>).hcaptcha;
  });

  it("rejects when hCaptcha's execute fails", async () => {
    (window as unknown as Record<string, unknown>).hcaptcha = {
      render: () => "h1",
      execute: () => Promise.reject("challenge-closed"),
      reset() {},
      remove() {},
    };
    const gate = createBotGate(window, document.createElement("div"), { provider: "hcaptcha", sitekey: "S", load: loadOk })!;
    await expect(gate.token()).rejects.toThrow("challenge-closed");
    delete (window as unknown as Record<string, unknown>).hcaptcha;
  });

  it("loads the provider's script once per window, not once per page (an element in an iframe)", async () => {
    const frame = document.createElement("iframe");
    document.body.append(frame);
    const other = frame.contentWindow!;
    const appended = (w: Window) => vi.spyOn(w.document.head, "append").mockImplementation(((s: HTMLScriptElement) => void queueMicrotask(() => s.onload?.(new Event("load")))) as never);
    const here = appended(window);
    const there = appended(other);
    const gates = [window, other].map((w) => createBotGate(w, w.document.createElement("div"), { provider: "turnstile", sitekey: "S", script: "https://gate.test/own-url.js" })!);
    await Promise.all(gates.map((g) => g.warm().catch(() => {}))); // no turnstile on either window: render fails, loading is what is asserted
    await Promise.all(gates.map((g) => g.warm().catch(() => {})));
    expect(here).toHaveBeenCalledTimes(1);
    expect(there).toHaveBeenCalledTimes(1);
  });

  it("warns and gives nothing for an unknown provider or an empty sitekey", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const box = document.createElement("div");
    expect(createBotGate(window, box, { provider: "turnstle", sitekey: "S" })).toBeNull();
    expect(createBotGate(window, box, { provider: "turnstile", sitekey: "" })).toBeNull();
    expect(warn.mock.calls.map((c) => String(c[0]))).toEqual([expect.stringContaining("turnstile"), expect.stringContaining("sitekey")]);
  });
});

describe("<heed-feedback> with a bot gate", () => {
  const mount = (attrs: Record<string, string>) => {
    const el = document.createElement(TAG) as HeedFeedbackElement;
    for (const [k, v] of Object.entries({ endpoint: "https://h.example/r", screenshot: "false", ...attrs })) el.setAttribute(k, v);
    document.body.append(el);
    return el;
  };
  /** jsdom never runs scripts: make appending the provider's <script> count as loaded. */
  const scriptsLoad = () =>
    vi.spyOn(document.head, "append").mockImplementation(((s: HTMLScriptElement) => void queueMicrotask(() => s.onload?.(new Event("load")))) as never);
  const send = async (el: HeedFeedbackElement, title = "t") => {
    const root = el.shadowRoot!;
    (root.querySelector("input[name=title]") as HTMLInputElement).value = title;
    (root.querySelector("form") as HTMLFormElement).requestSubmit();
    const button = root.querySelector("button[type=submit]") as HTMLButtonElement;
    await vi.waitFor(() => expect(button.disabled).toBe(false));
  };
  const okFetch = () =>
    vi.fn(async (_url: unknown, _init?: unknown) => new Response(JSON.stringify({ ok: true, id: "r1", status: "received" }), { status: 200 }));
  const tokenSent = (f: ReturnType<typeof okFetch>, n = 0) => (f.mock.calls[n][1] as { body: FormData }).body.get("bot_token");

  it("is off by default: no script, no challenge, no empty box", () => {
    const append = scriptsLoad();
    const el = mount({});
    el.open();
    expect(append).not.toHaveBeenCalled();
    expect((el.shadowRoot!.querySelector(".gate") as HTMLElement).children).toHaveLength(0);
  });

  it("loads the provider on open and sends its token with the report", async () => {
    const fake = fakeTurnstile();
    (window as unknown as Record<string, unknown>).turnstile = fake.api;
    const append = scriptsLoad();
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    const el = mount({ "bot-gate": "turnstile", "bot-sitekey": "SITE" });
    el.open();
    await vi.waitFor(() => expect(fake.api.render).toHaveBeenCalled());
    expect((append.mock.calls[0][0] as HTMLScriptElement).src).toBe(GATES.turnstile.script);
    expect(el.shadowRoot!.querySelector(".gate")!.children).toHaveLength(1); // rendered inside the dialog
    await send(el);
    expect(tokenSent(fetchMock)).toBe("tok1");
  });

  it("an explicit botToken provider wins over the built-in gate", async () => {
    const append = scriptsLoad();
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    const el = mount({ "bot-gate": "turnstile", "bot-sitekey": "SITE" });
    el.botToken = () => "mine";
    el.open();
    await send(el);
    expect(tokenSent(fetchMock)).toBe("mine");
    expect(append).not.toHaveBeenCalled();
  });

  it("a challenge that fails is reported as the bot error, without sending", async () => {
    (window as unknown as Record<string, unknown>).turnstile = fakeTurnstile((cbs) => cbs["error-callback"]("110200")).api;
    scriptsLoad();
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    const el = mount({ "bot-gate": "turnstile", "bot-sitekey": "SITE" });
    el.open();
    await send(el);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(el.shadowRoot!.querySelector(".status.error")!.textContent).toContain("confirm you are human");
  });

  it("tells the visitor to complete the check while a challenge waits", async () => {
    (window as unknown as Record<string, unknown>).turnstile = fakeTurnstile(() => {}).api; // waits on the visitor
    scriptsLoad();
    vi.stubGlobal("fetch", okFetch());
    const el = mount({ "bot-gate": "turnstile", "bot-sitekey": "SITE" });
    el.open();
    (el.shadowRoot!.querySelector("input[name=title]") as HTMLInputElement).value = "t";
    (el.shadowRoot!.querySelector("form") as HTMLFormElement).requestSubmit();
    await vi.waitFor(() => expect(el.shadowRoot!.querySelector(".status")!.textContent).toContain("complete the check"));
  });

  it("closing the dialog mid-challenge cancels the send quietly and frees Send at once", async () => {
    (window as unknown as Record<string, unknown>).turnstile = fakeTurnstile(() => {}).api; // waits on the visitor
    scriptsLoad();
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    const el = mount({ "bot-gate": "turnstile", "bot-sitekey": "SITE" });
    const errors: string[] = [];
    el.addEventListener("heed:error", () => errors.push("error"));
    el.open();
    const root = el.shadowRoot!;
    (root.querySelector("input[name=title]") as HTMLInputElement).value = "t";
    (root.querySelector("form") as HTMLFormElement).requestSubmit();
    const button = root.querySelector("button[type=submit]") as HTMLButtonElement;
    await vi.waitFor(() => expect(root.querySelector(".status")!.textContent).toContain("complete the check"));
    expect(button.disabled).toBe(true);
    el.close();
    await vi.waitFor(() => expect(button.disabled).toBe(false));
    expect(root.querySelector(".status")!.textContent).toBe("");
    expect(root.querySelector(".status.error")!.textContent).toBe("");
    expect(errors).toEqual([]);
    expect(fetchMock).not.toHaveBeenCalled();
    expect((root.querySelector("input[name=title]") as HTMLInputElement).value).toBe("t"); // the draft stays
  });

  it("closing the dialog while the report uploads (the token is already spent) still reports the result", async () => {
    (window as unknown as Record<string, unknown>).turnstile = fakeTurnstile().api;
    scriptsLoad();
    let answer!: (r: Response) => void;
    const fetchMock = vi.fn(() => new Promise<Response>((r) => (answer = r)));
    vi.stubGlobal("fetch", fetchMock);
    const el = mount({ "bot-gate": "turnstile", "bot-sitekey": "SITE" });
    const submitted = vi.fn();
    el.addEventListener("heed:submitted", submitted);
    el.open();
    const root = el.shadowRoot!;
    (root.querySelector("input[name=title]") as HTMLInputElement).value = "t";
    (root.querySelector("form") as HTMLFormElement).requestSubmit();
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    el.close();
    answer(new Response(JSON.stringify({ ok: true, id: "r1", status: "received" }), { status: 200 }));
    await vi.waitFor(() => expect(submitted).toHaveBeenCalledTimes(1));
    expect((root.querySelector(".done") as HTMLElement).hidden).toBe(false);
    expect((root.querySelector("input[name=title]") as HTMLInputElement).value).toBe(""); // the draft was sent
  });

  /** An open dialog whose screenshot has been taken and shown (jsdom loads no images: play the browser's part). */
  const withShot = async (el: HeedFeedbackElement) => {
    el.configure({ takeScreenshot: async () => new Blob([new Uint8Array([137, 80, 78, 71])], { type: "image/png" }) });
    el.open();
    const root = el.shadowRoot!;
    await vi.waitFor(() => expect(root.querySelector(".preview")!.getAttribute("src")).toBeTruthy());
    root.querySelector(".preview")!.dispatchEvent(new Event("load"));
    (root.querySelector("input[name=title]") as HTMLInputElement).value = "t";
    return root;
  };
  const tooLarge = () => new Response(JSON.stringify({ detail: "screenshot too large" }), { status: 413 });

  it("resends without the screenshot behind a second challenge, and says why while it runs", async () => {
    const fake = fakeTurnstile();
    (window as unknown as Record<string, unknown>).turnstile = fake.api;
    scriptsLoad();
    const fetchMock = vi.fn(async (_u: unknown, init?: unknown) => ((init as { body: FormData }).body.has("screenshot") ? tooLarge() : new Response(JSON.stringify({ ok: true, id: "r1", status: "received" }), { status: 200 })));
    vi.stubGlobal("fetch", fetchMock);
    const el = mount({ "bot-gate": "turnstile", "bot-sitekey": "SITE", screenshot: "true" });
    const root = await withShot(el);
    const lines: string[] = [];
    new MutationObserver(() => lines.push(root.querySelector(".status")!.textContent ?? "")).observe(root.querySelector(".status")!, { childList: true, subtree: true, characterData: true });
    (root.querySelector("form") as HTMLFormElement).requestSubmit();
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fake.api.execute).toHaveBeenCalledTimes(2);
    expect(tokenSent(fetchMock as ReturnType<typeof okFetch>, 1)).toBe("tok2");
    expect(lines.filter((l) => l.includes("too large") && l.includes("complete the check"))).not.toHaveLength(0);
    expect(lines.filter((l) => l.includes("too large") && l.includes("Sending"))).not.toHaveLength(0);
  });

  it("does not start a second challenge for the screenshot-less resend once the dialog is closed", async () => {
    const fake = fakeTurnstile();
    (window as unknown as Record<string, unknown>).turnstile = fake.api;
    scriptsLoad();
    let answer!: (r: Response) => void;
    const fetchMock = vi.fn(() => new Promise<Response>((r) => (answer = r)));
    vi.stubGlobal("fetch", fetchMock);
    const el = mount({ "bot-gate": "turnstile", "bot-sitekey": "SITE", screenshot: "true" });
    const root = await withShot(el);
    (root.querySelector("form") as HTMLFormElement).requestSubmit();
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    el.close();
    answer(tooLarge());
    const button = root.querySelector("button[type=submit]") as HTMLButtonElement;
    await vi.waitFor(() => expect(button.disabled).toBe(false));
    expect(fake.api.execute).toHaveBeenCalledTimes(1); // only the first send's challenge
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(root.querySelector(".status.error")!.textContent).toBe("");
  });

  it("is rebuilt when the sitekey changes and removed with the element", async () => {
    const fake = fakeTurnstile();
    (window as unknown as Record<string, unknown>).turnstile = fake.api;
    scriptsLoad();
    const el = mount({ "bot-gate": "turnstile", "bot-sitekey": "A" });
    el.open();
    await vi.waitFor(() => expect(fake.api.render).toHaveBeenCalledTimes(1));
    el.setAttribute("bot-sitekey", "B");
    el.close();
    el.open();
    await vi.waitFor(() => expect(fake.api.render).toHaveBeenCalledTimes(2));
    expect(fake.api.render.mock.calls[1][1]).toMatchObject({ sitekey: "B" });
    expect(fake.api.remove).toHaveBeenCalledTimes(1);
    el.remove();
    expect(fake.api.remove).toHaveBeenCalledTimes(2);
  });
});
