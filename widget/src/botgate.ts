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
 * Providers are entries of `GATES` (strategy pattern): `turnstile` and `hcaptcha`, and
 * another is one more entry, not a new branch. The challenge renders inside the dialog, not outside the
 * shadow root: a modal `<dialog>` makes everything outside itself inert, so an
 * interactive challenge placed outside could not be clicked.
 *
 * Under a CSP the page needs the provider's origins (see embedding-security.md); under
 * Trusted Types it also needs the policy named `heed-bot-gate`, which passes only the
 * provider's script URL.
 */
import { ownScript } from "./sibling.js";

/** What Turnstile's `api.js` puts on `window.turnstile` (the part heed uses). */
interface TurnstileApi {
  render(container: HTMLElement, params: Record<string, unknown>): string | undefined;
  execute(widgetId: string): void;
  reset(widgetId: string): void;
  remove(widgetId: string): void;
}

/** What hCaptcha's `api.js` puts on `window.hcaptcha` (the part heed uses). */
interface HCaptchaApi {
  render(container: HTMLElement, params: Record<string, unknown>): string | undefined;
  execute(widgetId: string, options: { async: true }): Promise<{ response: string }>;
  reset(widgetId: string): void;
  remove(widgetId: string): void;
}

interface ChallengeCallbacks {
  onToken(token: string): void;
  onError(code: string): void;
  /** The challenge needs the visitor (true) or is done with them (false): the box shows only then. */
  onInteractive(on: boolean): void;
}

/** One challenge provider: where its script lives and how to drive its widget. */
export interface GateProvider {
  /** Script that defines the provider's global API. */
  script: string;
  /** Render a widget into `container`, ready to `execute`; returns the handle to pass on. */
  render(win: Window, container: HTMLElement, sitekey: string, cb: ChallengeCallbacks): string | undefined;
  /** Run the challenge; a token reaches `cb.onToken`. `fresh` is false for the first run. */
  run(win: Window, handle: string | undefined, fresh: boolean, cb: ChallengeCallbacks): void;
  remove(win: Window, handle: string | undefined): void;
}

const turnstile = (win: Window) => (win as unknown as { turnstile?: TurnstileApi }).turnstile;
const hcaptcha = (win: Window) => (win as unknown as { hcaptcha?: HCaptchaApi }).hcaptcha;

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
        "before-interactive-callback": () => cb.onInteractive(true),
        "after-interactive-callback": () => cb.onInteractive(false),
      }),
    run(win, handle, fresh) {
      if (handle === undefined) return;
      if (fresh) turnstile(win)?.reset(handle);
      turnstile(win)?.execute(handle);
    },
    remove: (win, handle) => handle !== undefined && turnstile(win)?.remove(handle),
  },
  hcaptcha: {
    script: "https://js.hcaptcha.com/1/api.js?render=explicit",
    render: (win, container, sitekey, cb) =>
      hcaptcha(win)?.render(container, {
        sitekey,
        size: "invisible",
        // Without this the challenge is appended to <body>, which a modal dialog makes inert.
        "challenge-container": container,
        "error-callback": (code: string) => cb.onError(String(code)),
        "open-callback": () => cb.onInteractive(true),
        "close-callback": () => (cb.onInteractive(false), cb.onError("closed")),
      }),
    run(win, handle, fresh, cb) {
      if (handle === undefined) return;
      if (fresh) hcaptcha(win)?.reset(handle);
      hcaptcha(win)
        ?.execute(handle, { async: true })
        .then((r) => cb.onToken(r.response), (e) => cb.onError(String(e)));
    },
    remove: (win, handle) => handle !== undefined && hcaptcha(win)?.remove(handle),
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
  /** Give up on the token being waited for (the visitor left); false when there was none. */
  cancel(): boolean;
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
  // Shown only while the challenge needs the visitor: an invisible one leaves no gap in the form.
  container.hidden = true;
  const callbacks: ChallengeCallbacks = {
    // The request is settled (and forgotten) at once: a later cancel() must not touch a send that has its token.
    onToken: (t) => settle()?.resolve(t),
    onError: (code) => settle()?.reject(new Error(`challenge failed: ${code}`)),
    onInteractive: (on) => (container.hidden = !(on && pending)), // nobody is waiting on a cancelled challenge
  };
  const settle = () => {
    const had = pending;
    pending = null;
    return had;
  };
  const cancel = (reason: string) => {
    const had = settle();
    container.hidden = true;
    had?.reject(new Error(reason));
    return !!had;
  };
  let handle: string | undefined;
  let ready: Promise<void> | null = null;
  let used = false;
  let gone = false;

  const warm = (): Promise<void> => {
    ready ??= load(win, options.script ?? provider.script).then(() => {
      if (gone) return;
      handle = provider.render(win, container, options.sitekey, callbacks);
      if (handle === undefined) throw new Error("the challenge widget did not render");
    });
    ready.catch(() => (ready = null)); // a later open may retry
    return ready;
  };
  return {
    warm,
    token() {
      cancel("a newer challenge replaced this one");
      // Pending from the start, so a cancel() while the script still loads is heard.
      return new Promise<string>((resolve, reject) => {
        const request = { resolve, reject };
        pending = request;
        warm().then(() => {
          if (pending !== request) return; // cancelled, replaced or removed meanwhile: already rejected
          provider.run(win, handle, used, callbacks);
          used = true;
        }).catch((e) => pending === request && (settle()!.reject(e), undefined));
      });
    },
    cancel: () => cancel("the challenge was cancelled"),
    destroy() {
      gone = true;
      cancel("the bot gate was removed");
      if (handle !== undefined) provider.remove(win, handle);
      container.replaceChildren();
    },
  };
}

type Policy = { createScriptURL(u: string): unknown };
type TT = { createPolicy(n: string, p: object): Policy };
const POLICY = "heed-bot-gate";
const allowed = new Set<string>();
// Per window: an element can live in another document (an iframe), whose `window.turnstile` is its own.
const loaded = new WeakMap<Window, Map<string, Promise<void>>>();
const policies = new WeakMap<Window, Policy | null>();

/**
 * Nonce lent to the provider's script (strict-dynamic aside): that of the script which loaded this
 * bundle (for its own document), else of any nonced script in the document (an ES-module import has no
 * `currentScript`).
 */
const nonceOf = (win: Window): string =>
  (win.document === globalThis.document ? ownScript?.nonce : "") || win.document.querySelector<HTMLScriptElement>("script[nonce]")?.nonce || "";

/** The script URL, as a Trusted Types value when the page enforces them (only URLs this module loads pass). */
function trustedUrl(win: Window, url: string): string {
  allowed.add(url);
  if (!policies.has(win)) {
    try {
      policies.set(win, (win as unknown as { trustedTypes?: TT }).trustedTypes?.createPolicy(POLICY, { createScriptURL: (u: string) => (allowed.has(u) ? u : "") }) ?? null);
    } catch {
      policies.set(win, null); // the page's trusted-types list does not name the policy
    }
  }
  const policy = policies.get(win);
  return (policy ? policy.createScriptURL(url) : url) as string;
}

/** Load a classic script once per URL. */
function loadScript(win: Window, url: string): Promise<void> {
  const cache = loaded.get(win) ?? loaded.set(win, new Map()).get(win)!;
  const cached = cache.get(url);
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
    s.nonce = nonceOf(win);
    s.onload = () => resolve();
    s.onerror = () => reject(new Error(`could not load ${url}`));
    (doc.head || doc.documentElement).append(s);
  });
  cache.set(url, p);
  p.catch(() => cache.delete(url));
  return p;
}
