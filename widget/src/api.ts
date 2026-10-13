/**
 * `window.heed`: the imperative API, and the auto-mount driven by the loader.
 *
 *   heed.init({ endpoint, label, ... })  // mount (or reconfigure) the widget
 *   heed.open() / heed.close()           // show or hide the dialog
 *   heed.on("submitted", fn)             // listen; returns an unsubscribe function
 *   heed.destroy()                       // remove the widget heed mounted
 *   heed("open")                         // the same calls, in queue form
 *
 * Calls made before the bundle arrived were buffered by the loader (or by an inline
 * stub); they are replayed here in order. The loader's own `_boot` call comes first:
 * it carries the loader's URL (the default endpoint is the `report` route next to the
 * `widget/` folder it was served from) and its `data-*` attributes, and mounts the
 * widget unless `data-auto="false"`.
 */
import { HeedFeedbackElement, TAG } from "./element";
import { type HeedOptions, cleanOptions, optionsFromAttributes } from "./options";

declare const __HEED_VERSION__: string;

export interface HeedApi {
  (command: string, ...args: unknown[]): unknown;
  init(options?: Partial<HeedOptions>): HeedFeedbackElement | null;
  open(): void;
  close(): void;
  destroy(): void;
  on(event: string, handler: (detail: unknown, event: CustomEvent) => void): () => void;
  version: string;
  loaded: true;
}

interface Boot {
  base?: string;
  data?: Record<string, string>;
}

type HeedWindow = Window & { heed?: Partial<HeedApi> & { q?: ArrayLike<unknown>[] } };

export function installApi(win: HeedWindow = window as HeedWindow): HeedApi {
  if (win.heed?.loaded) return win.heed as HeedApi;
  const doc = win.document;
  let managed: HeedFeedbackElement | null = null;
  let defaults: Partial<HeedOptions> = {};

  const attach = (el: HeedFeedbackElement) => {
    if (doc.body) doc.body.append(el);
    else doc.addEventListener("DOMContentLoaded", () => managed === el && doc.body.append(el), { once: true });
  };

  const init = (options: Partial<HeedOptions> = {}): HeedFeedbackElement | null => {
    if (!managed) {
      managed = doc.createElement(TAG) as HeedFeedbackElement;
      managed.setAttribute("data-heed-managed", "");
      managed.configure(defaults);
      attach(managed);
    }
    return managed.configure(cleanOptions(options as Record<string, unknown>));
  };

  /** The widget heed mounted, else one the page wrote in its markup, else a new one. */
  const target = (): HeedFeedbackElement | null =>
    managed ?? (doc.querySelector(TAG) as HeedFeedbackElement | null) ?? init();

  const boot = ({ base, data = {} }: Boot = {}) => {
    defaults = cleanOptions({
      ...(base ? { endpoint: new URL("../report", base).href } : {}),
      ...optionsFromAttributes((attr) => data[attr]),
    });
    if (data.auto !== "false") init();
  };

  const commands: Record<string, (...args: never[]) => unknown> = {
    _boot: boot,
    init,
    open: () => {
      const el = target();
      if (el && !el.isConnected) doc.addEventListener("DOMContentLoaded", () => el.open(), { once: true });
      else el?.open();
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

  const queued = Array.from(win.heed?.q ?? [], (args) => Array.from(args));
  const api = Object.assign((command: string, ...args: unknown[]) => run(command, ...args), {
    init: (o?: Partial<HeedOptions>) => run("init", o) as HeedFeedbackElement | null,
    open: () => void run("open"),
    close: () => void run("close"),
    destroy: () => void run("destroy"),
    on: (event: string, handler: (detail: unknown, event: CustomEvent) => void) => run("on", event, handler) as () => void,
    version: __HEED_VERSION__,
    loaded: true as const,
  });
  win.heed = api;
  for (const [command, ...args] of queued) run(command, ...args);
  return api;
}
