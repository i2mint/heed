/**
 * `window.heed`: the imperative API, and the auto-mount driven by the loader.
 *
 *   heed.init({ endpoint, label, ... })  // mount (or reconfigure) the widget
 *   heed.open() / heed.close()           // show or hide the dialog
 *   heed.on("submitted", fn)             // listen; returns an unsubscribe function
 *   heed.ready(fn)                       // fn(heed) once the bundle has loaded
 *   heed.destroy()                       // remove the widget heed mounted
 *   heed("open")                         // the same calls, in queue form
 *
 * Calls made before the bundle arrived were buffered by the loader (or by an inline
 * stub); they are replayed here in order. The loader's own `_boot` call comes first:
 * it carries the loader's URL (the default endpoint is the `report` route next to the
 * `widget/` folder it was served from) and its `data-*` attributes, and mounts the
 * widget unless `data-auto="false"` or the page already wrote a `<heed-feedback>`.
 *
 * The mounted widget is put back if the page replaces `<body>` (Turbo, htmx boost,
 * pjax…), so the button survives client-side navigations.
 */
import { HeedFeedbackElement, TAG } from "./element.js";
import { type HeedOptions, cleanOptions, optionsFromAttributes, parseBool } from "./options.js";

declare const __HEED_VERSION__: string;

export interface HeedApi {
  (command: string, ...args: unknown[]): unknown;
  init(options?: Partial<HeedOptions>): HeedFeedbackElement | null;
  open(): void;
  close(): void;
  destroy(): void;
  on(event: string, handler: (detail: unknown, event: CustomEvent) => void): () => void;
  ready(callback: (api: HeedApi) => void): void;
  version: string;
  loaded: true;
  booted: boolean;
}

interface Boot {
  base?: string;
  data?: Record<string, string>;
}

type Entry = ArrayLike<unknown> & { off?: unknown };
type HeedWindow = Window & { heed?: Partial<HeedApi> & { q?: Entry[] } };

export function installApi(win: HeedWindow = window as HeedWindow): HeedApi {
  if (win.heed?.loaded) return win.heed as HeedApi;
  const doc = win.document;
  let managed: HeedFeedbackElement | null = null;
  let defaults: Partial<HeedOptions> = {};

  const whenParsed = (fn: () => void) => {
    if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", fn, { once: true });
    else fn();
  };
  const attach = (el: HeedFeedbackElement) =>
    whenParsed(() => {
      if (managed !== el || el.isConnected || !doc.body) return;
      // A restored snapshot of an old <body> (Turbo's cache) carries a dead copy.
      doc.querySelectorAll(`${TAG}[data-heed-managed]`).forEach((copy) => copy.remove());
      doc.body.append(el);
    });
  // Pages that swap <body>, or its contents, on navigation detach the widget: put it back.
  let watchedBody: HTMLElement | null = null;
  const observer = new MutationObserver(() => {
    if (doc.body && doc.body !== watchedBody) {
      watchedBody = doc.body;
      observer.observe(watchedBody, { childList: true });
    }
    if (managed && !managed.isConnected) attach(managed);
  });
  observer.observe(doc.documentElement, { childList: true });
  whenParsed(() => {
    watchedBody = doc.body;
    if (watchedBody) observer.observe(watchedBody, { childList: true });
  });

  const init = (options: Partial<HeedOptions> = {}): HeedFeedbackElement | null => {
    if (!managed) {
      managed = doc.createElement(TAG) as HeedFeedbackElement;
      managed.setAttribute("data-heed-managed", "");
      managed.configure(defaults);
    }
    attach(managed);
    return managed.configure(cleanOptions(options as Record<string, unknown>));
  };

  /** The widget heed mounted, else one the page wrote in its markup, else a new one. */
  const target = (): HeedFeedbackElement | null =>
    managed ?? (doc.querySelector(TAG) as HeedFeedbackElement | null) ?? init();

  const boot = ({ base, data = {} }: Boot = {}) => {
    api.booted = true;
    defaults = cleanOptions({
      ...(base ? { endpoint: new URL("../report", base).href } : {}),
      ...optionsFromAttributes((attr) => data[attr]),
    });
    if (data.auto !== undefined && parseBool(data.auto) === false) return;
    whenParsed(() => {
      const own = managed ? null : (doc.querySelector(TAG) as HeedFeedbackElement | null);
      if (!own) init();
      // The page wrote its own element: leave it be, but lend it the loader's endpoint.
      else if (!own.hasAttribute("endpoint") && !own.options.endpoint && defaults.endpoint) own.configure({ endpoint: defaults.endpoint });
    });
  };

  const commands: Record<string, (...args: never[]) => unknown> = {
    _boot: boot,
    init,
    open: () => {
      const el = target();
      if (el) whenParsed(() => el.open());
    },
    close: () => (managed ?? (doc.querySelector(TAG) as HeedFeedbackElement | null))?.close(),
    destroy: () => {
      managed?.close();
      managed?.remove();
      managed = null;
    },
    on: (event: string, handler: (detail: unknown, event: CustomEvent) => void) => {
      const type = event.startsWith("heed:") ? event : `heed:${event}`;
      const listener = (e: Event) => handler((e as CustomEvent).detail, e as CustomEvent);
      doc.addEventListener(type, listener);
      return () => doc.removeEventListener(type, listener);
    },
    ready: (callback: (api: HeedApi) => void) => callback(api),
  };

  const run = (command: unknown, ...args: unknown[]): unknown => {
    const fn = typeof command === "string" ? commands[command] : undefined;
    if (!fn) {
      console.warn(`heed: unknown command ${String(command)}`);
      return undefined;
    }
    try {
      return (fn as (...a: unknown[]) => unknown)(...args);
    } catch (e) {
      console.error(`heed: ${command} failed`, e);
      return undefined;
    }
  };

  const stubQueue = win.heed?.q;
  const queued: Entry[] = Array.from(stubQueue ?? []);
  // Empty the stub's queue, so an unsubscribe it handed out finds the real listener.
  if (Array.isArray(stubQueue)) stubQueue.length = 0;
  const api: HeedApi = Object.assign((command: string, ...args: unknown[]) => run(command, ...args), {
    init: (o?: Partial<HeedOptions>) => run("init", o) as HeedFeedbackElement | null,
    open: () => void run("open"),
    close: () => void run("close"),
    destroy: () => void run("destroy"),
    on: (event: string, handler: (detail: unknown, event: CustomEvent) => void) => run("on", event, handler) as () => void,
    ready: (callback: (api: HeedApi) => void) => void run("ready", callback),
    version: __HEED_VERSION__,
    loaded: true as const,
    booted: false,
  });
  win.heed = api;
  for (const entry of queued) {
    const [command, ...args] = Array.from(entry);
    const result = run(command, ...args);
    // Lets an unsubscribe the loader's stub handed out reach the real listener.
    if (command === "on") entry.off = result;
  }
  return api;
}
