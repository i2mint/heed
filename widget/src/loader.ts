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

type Stub = ((...args: unknown[]) => void) & { q?: ArrayLike<unknown>[]; loaded?: boolean; [method: string]: unknown };

(() => {
  const w = window as unknown as Window & { heed?: Stub };
  const d = document;
  if (w.heed?.loaded) return; // the bundle is already running
  const self = (d.currentScript as HTMLScriptElement | null) ?? findSelf();
  if (!self?.src) {
    console.warn("heed: could not find the loader's <script> tag");
    return;
  }
  let requested = false;
  const load = () => {
    if (requested) return;
    requested = true;
    const s = d.createElement("script");
    const url = new URL(__HEED_BUNDLE__, self.src).href;
    // Under `require-trusted-types-for 'script'` a script URL must come from a policy;
    // this one only ever passes the bundle's own URL.
    const tt = (w as unknown as { trustedTypes?: { createPolicy(n: string, p: object): { createScriptURL(u: string): string } } }).trustedTypes;
    let src: string = url;
    try {
      if (tt) src = tt.createPolicy("heed", { createScriptURL: (u: string) => (u === url ? u : "") }).createScriptURL(url);
    } catch {
      // a CSP `trusted-types` list without "heed": fall back to the plain URL
    }
    s.src = src;
    s.integrity = __HEED_INTEGRITY__;
    s.crossOrigin = "anonymous";
    s.async = true;
    s.onerror = () => console.warn(`heed: could not load ${s.src}`);
    (d.head || d.documentElement).append(s);
  };
  const queue = w.heed?.q ?? [];
  const stub = ((...args: unknown[]) => {
    stub.q!.push(args);
    if (args[0] === "open") load();
  }) as Stub;
  stub.q = queue;
  for (const m of ["init", "open", "close", "destroy", "on"]) stub[m] = (...a: unknown[]) => stub(m, ...a);
  const data: Record<string, string> = {};
  for (const a of Array.from(self.attributes)) if (a.name.startsWith("data-")) data[a.name.slice(5)] = a.value;
  // Boot first, so the page's own queued calls refine the data-* options.
  (queue as unknown[]).unshift(["_boot", { base: self.src, data }]);
  w.heed = stub;
  if (queue.some((args) => args[0] === "open")) load();
  const idle = () =>
    "requestIdleCallback" in w ? w.requestIdleCallback(load, { timeout: 2000 }) : setTimeout(load, 1);
  if (d.readyState === "complete") idle();
  else w.addEventListener("load", idle, { once: true });

  function findSelf(): HTMLScriptElement | null {
    const all = Array.from(d.querySelectorAll<HTMLScriptElement>("script[src]"));
    return all.find((s) => /\/heed(-[0-9a-f]{10})?\.js([?#]|$)/.test(s.src)) ?? null;
  }
})();
