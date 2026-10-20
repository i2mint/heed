/**
 * heed's loader: the one `<script>` tag a site adds.
 *
 *   <script async src="https://feedback.example/heed/widget/heed.js"></script>
 *
 * It is tiny and does three things:
 *   1. installs `window.heed`, a stub that buffers calls (`heed("open")`,
 *      `heed.init({...})`) until the widget bundle arrives, adopting the queue of an
 *      inline stub the page may have defined first;
 *   2. records its own `data-*` attributes and URL, so the bundle can auto-mount the
 *      widget with those options and default the endpoint to the backend that served
 *      this file;
 *   3. loads the widget bundle once the page has finished loading (when the browser is
 *      idle), or at once if the page asks to open the widget. The bundle's
 *      content-hashed name and SRI hash are baked in at build time, so pinning this
 *      loader with SRI pins the whole chain.
 */
declare const __HEED_BUNDLE__: string;
declare const __HEED_INTEGRITY__: string;

type Entry = ArrayLike<unknown> & { off?: unknown };
type Policy = { createScriptURL(u: string): unknown };
type Stub = ((...args: unknown[]) => unknown) & { q?: Entry[]; loaded?: boolean; booted?: boolean; loading?: boolean; [method: string]: unknown };
type TT = { createPolicy(n: string, p: object): Policy };

(() => {
  const w = window as unknown as Window & { heed?: Stub; trustedTypes?: TT; __heedTT?: Policy | null };
  const d = document;
  const warn = (m: string) => console.warn(`heed: ${m}`);
  const TWICE = "loader included twice; ignoring this one";
  const self = (d.currentScript as HTMLScriptElement | null) ?? findSelf();
  if (!self?.src) return warn("loader tag not found; add data-heed-loader to it");
  const data: Record<string, string> = {};
  for (const a of Array.from(self.attributes)) if (a.name.startsWith("data-")) data[a.name.slice(5)] = a.value;
  const boot = ["_boot", { base: self.src, data }];
  // An element with id="heed" shows up as window.heed (named access): not a real one.
  const named = w.heed instanceof Element || w.heed instanceof HTMLCollection;
  const existing = named ? undefined : w.heed;
  if (existing !== undefined && (typeof existing !== "function" || (existing.q !== undefined && !Array.isArray(existing.q)))) {
    return warn("window.heed is taken; not loading");
  }
  if (existing?.loaded) {
    // The bundle was loaded directly before this loader: hand it our options once.
    return existing.booted ? warn(TWICE) : void existing(...boot);
  }
  if (existing?.loading) return warn(TWICE);

  const queue: Entry[] = existing?.q ?? [];
  const stub = ((...args: unknown[]) => {
    stub.q!.push(args);
    if (args[0] === "open") load();
  }) as Stub;
  stub.q = queue;
  stub.loading = true;
  for (const m of ["init", "open", "close", "destroy", "ready"]) stub[m] = (...a: unknown[]) => stub(m, ...a);
  // `on` answers at once with an unsubscribe that works before and after the bundle arrives.
  stub.on = (...a: unknown[]) => {
    const entry: Entry = ["on", ...a];
    queue.push(entry);
    return () => {
      const i = queue.indexOf(entry);
      if (i >= 0) queue.splice(i, 1);
      else if (typeof entry.off === "function") entry.off();
    };
  };
  // Boot first, so the page's own queued calls refine the data-* options.
  queue.unshift(boot);
  w.heed = stub;

  // Under `require-trusted-types-for 'script'` a script URL must come from a policy;
  // this one only passes heed's own content-hashed files next to this loader (the
  // bundle reuses it for the rasterizer and the translations).
  const url = new URL(__HEED_BUNDLE__, self.src).href;
  const dir = new URL(".", url).href;
  const own = (u: string) => (u.startsWith(dir) && /^heed-(widget|raster|locales|recorder)-[0-9a-f]{10}\.js$/.test(u.slice(dir.length)) ? u : "");
  let policy: Policy | null = null;
  try {
    policy = w.trustedTypes?.createPolicy("heed", { createScriptURL: own }) ?? null;
  } catch {
    // reported below, if the page enforces Trusted Types
  }
  w.__heedTT = policy;
  let requested = false;
  function load() {
    if (requested) return;
    requested = true;
    const s = d.createElement("script");
    try {
      s.src = (policy ? policy.createScriptURL(url) : url) as string;
    } catch {
      return warn("blocked by Trusted Types; allow the policy `heed` in the CSP trusted-types list");
    }
    s.integrity = __HEED_INTEGRITY__;
    s.crossOrigin = "anonymous";
    s.async = true;
    if (self!.nonce) s.nonce = self!.nonce;
    s.onerror = () => warn(`could not load ${url}`);
    (d.head || d.documentElement).append(s);
  }
  if (queue.some((args) => args[0] === "open")) load();
  const idle = () =>
    "requestIdleCallback" in w ? w.requestIdleCallback(load, { timeout: 2000 }) : setTimeout(load, 1);
  if (d.readyState === "complete") idle();
  else w.addEventListener("load", idle, { once: true });

  function findSelf(): HTMLScriptElement | null {
    const marked = d.querySelector<HTMLScriptElement>("script[data-heed-loader][src]");
    if (marked) return marked;
    const all = Array.from(d.querySelectorAll<HTMLScriptElement>("script[src]"));
    return all.find((s) => /\/heed(-[0-9a-f]{10})?\.js([?#]|$)/.test(s.src)) ?? null;
  }
})();
