/**
 * The built-in bot-gate adapter: heed's ingest route can demand a challenge token
 * (heed#5), and the widget already sends one when the embedder supplies a `botToken`
 * provider. This module is the optional batteries-included provider: it loads the
 * challenge provider's script lazily, renders its widget inside the dialog, and hands
 * back a fresh token for every send.
 *
 * Off by default (`botGate: ""`). Turn it on with `data-bot-gate="turnstile"` and
 * `data-bot-sitekey="…"`. An explicit `botToken` provider always wins over it.
 *
 * Providers are entries of `GATES` (strategy pattern): adding hCaptcha is one more
 * entry, not a new branch. The challenge renders inside the dialog, not outside the
 * shadow root: a modal `<dialog>` makes everything outside itself inert, so an
 * interactive challenge placed outside could not be clicked.
 *
 * Under a CSP the page needs the provider's origins (see embedding-security.md); under
 * Trusted Types it also needs the policy named `heed-bot-gate`, which passes only the
 * provider's script URL.
 */

/** What Turnstile's `api.js` puts on `window.turnstile` (the part heed uses). */
interface TurnstileApi {
  render(container: HTMLElement, params: Record<string, unknown>): string | undefined;
  execute(widgetId: string): void;
  reset(widgetId: string): void;
  remove(widgetId: string): void;
}

interface ChallengeCallbacks {
  onToken(token: string): void;
  onError(code: string): void;
}

/** One challenge provider: where its script lives and how to drive its widget. */
export interface GateProvider {
  /** Script that defines the provider's global API. */
  script: string;
  /** Render a widget into `container`, ready to `execute`; returns the handle to pass on. */
  render(win: Window, container: HTMLElement, sitekey: string, cb: ChallengeCallbacks): string | undefined;
  /** Run the challenge; a token reaches `cb.onToken`. `fresh` is false for the first run. */
  run(win: Window, handle: string | undefined, fresh: boolean): void;
  remove(win: Window, handle: string | undefined): void;
}

const turnstile = (win: Window) => (win as unknown as { turnstile?: TurnstileApi }).turnstile;

export const GATES: Record<string, GateProvider> = {
  turnstile: {
    script: "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit",
    render: (win, container, sitekey, cb) =>
      turnstile(win)?.render(container, {
        sitekey,
        // Nothing runs until a report is being sent, so the token is fresh (they live five
        // minutes and work once); only a challenge that needs the visitor shows anything.
        execution: "execute",
        appearance: "interaction-only",
        callback: cb.onToken,
        "error-callback": (code: string) => (cb.onError(String(code)), true),
        "timeout-callback": () => cb.onError("timeout"),
      }),
    run(win, handle, fresh) {
      if (handle === undefined) return;
      if (fresh) turnstile(win)?.reset(handle);
      turnstile(win)?.execute(handle);
    },
    remove: (win, handle) => handle !== undefined && turnstile(win)?.remove(handle),
  },
};

export interface BotGateOptions {
  provider: string;
  sitekey: string;
  /** Overrides the provider's script URL (a self-hosted proxy, or a stand-in in tests). */
  script?: string;
  /** How the script is loaded; injectable for tests. */
  load?: (win: Window, url: string) => Promise<void>;
}

export interface BotGate {
  /** Start loading the script and rendering the challenge, ahead of the first send. */
  warm(): Promise<void>;
  /** A fresh token; rejects when the challenge fails or its script cannot load. */
  token(): Promise<string>;
  destroy(): void;
}

/** A gate for `options`, or null (with a console warning) when they cannot work. */
export function createBotGate(win: Window, container: HTMLElement, options: BotGateOptions): BotGate | null {
  const provider = GATES[options.provider];
  if (!provider) {
    console.warn(`heed: unknown bot gate ${JSON.stringify(options.provider)}; known: ${Object.keys(GATES).join(", ")}`);
    return null;
  }
  if (!options.sitekey) {
    console.warn("heed: bot-gate is set but bot-sitekey is empty; no challenge will run");
    return null;
  }
  const load = options.load ?? loadScript;
  let pending: { resolve(t: string): void; reject(e: Error): void } | null = null;
  let handle: string | undefined;
  let ready: Promise<void> | null = null;
  let used = false;
  let gone = false;

  const warm = (): Promise<void> => {
    ready ??= load(win, options.script ?? provider.script).then(() => {
      if (gone) return;
      handle = provider.render(win, container, options.sitekey, {
        onToken: (t) => pending?.resolve(t),
        onError: (code) => pending?.reject(new Error(`challenge failed: ${code}`)),
      });
      if (handle === undefined) throw new Error("the challenge widget did not render");
    });
    ready.catch(() => (ready = null)); // a later open may retry
    return ready;
  };
  return {
    warm,
    async token() {
      await warm();
      if (gone) throw new Error("the bot gate was removed");
      return new Promise<string>((resolve, reject) => {
        pending = { resolve, reject };
        provider.run(win, handle, used);
        used = true;
      });
    },
    destroy() {
      gone = true;
      pending?.reject(new Error("the bot gate was removed"));
      pending = null;
      if (handle !== undefined) provider.remove(win, handle);
      container.replaceChildren();
    },
  };
}

type Policy = { createScriptURL(u: string): unknown };
type TT = { createPolicy(n: string, p: object): Policy };
const POLICY = "heed-bot-gate";
const loaded = new Map<string, Promise<void>>();
const allowed = new Set<string>();
let policy: Policy | null | undefined;

/** Nonce of the script that loaded this bundle: lent to the provider's script (strict-dynamic aside). */
const nonce = (typeof document !== "undefined" && (document.currentScript as HTMLScriptElement | null)?.nonce) || "";

/** The script URL, as a Trusted Types value when the page enforces them (only URLs this module loads pass). */
function trustedUrl(win: Window, url: string): string {
  allowed.add(url);
  if (policy === undefined) {
    try {
      policy = (win as unknown as { trustedTypes?: TT }).trustedTypes?.createPolicy(POLICY, { createScriptURL: (u: string) => (allowed.has(u) ? u : "") }) ?? null;
    } catch {
      policy = null; // the page's trusted-types list does not name the policy
    }
  }
  return (policy ? policy.createScriptURL(url) : url) as string;
}

/** Load a classic script once per URL. */
function loadScript(win: Window, url: string): Promise<void> {
  const cached = loaded.get(url);
  if (cached) return cached;
  const p = new Promise<void>((resolve, reject) => {
    const doc = win.document;
    const s = doc.createElement("script");
    try {
      s.src = trustedUrl(win, url);
    } catch {
      return reject(new Error(`blocked by Trusted Types; allow a policy named ${POLICY}`));
    }
    s.async = true;
    if (nonce) s.nonce = nonce;
    s.onload = () => resolve();
    s.onerror = () => reject(new Error(`could not load ${url}`));
    (doc.head || doc.documentElement).append(s);
  });
  loaded.set(url, p);
  p.catch(() => loaded.delete(url));
  return p;
}
