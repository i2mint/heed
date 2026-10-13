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
