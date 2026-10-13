/**
 * Loading a file that sits next to the widget bundle (the screenshot rasterizer, the
 * translations): located from the bundle's own `<script>`, pinned by the SRI hash baked
 * in at build time (the same chain as loader → bundle), and passed through the
 * loader's Trusted Types policy `heed` when the page enforces them.
 */
type Policy = { createScriptURL(u: string): unknown };
type TT = { createPolicy(n: string, p: object): Policy };
type HeedGlobals = Window & { __heedTT?: Policy | null; trustedTypes?: TT };

/** The names heed's own files may have: the loader's policy and ours pass only these. */
export const OWN_FILE = /^heed-(widget|raster|locales)-[0-9a-f]{10}\.js$/;

/** This bundle's own `<script>`; null when an app bundled heed into its own code. */
export const ownScript = (() => {
  const s = typeof document !== "undefined" ? (document.currentScript as HTMLScriptElement | null) : null;
  return s && /\/heed-widget(-[0-9a-f]{10})?\.js([?#]|$)/.test(s.src) ? s : null;
})();

/** A Trusted Types-safe script URL: through the loader's `heed` policy, or our own. */
function scriptUrl(w: HeedGlobals, url: string): string {
  if (w.__heedTT === undefined) {
    const dir = new URL(".", url).href;
    const own = (u: string) => (u.startsWith(dir) && OWN_FILE.test(u.slice(dir.length)) ? u : "");
    try {
      w.__heedTT = w.trustedTypes?.createPolicy("heed", { createScriptURL: own }) ?? null;
    } catch {
      w.__heedTT = null; // the page's CSP does not allow the policy
    }
  }
  return (w.__heedTT ? w.__heedTT.createScriptURL(url) : url) as string;
}

/**
 * Load `file` (a name relative to the bundle) once and resolve with what it registered,
 * as `read()` reports it. Rejects when there is no bundle `<script>` to locate it from.
 */
export function loadSibling<T>(win: Window, file: string, integrity: string, read: () => T | undefined, what: string): Promise<T> {
  const have = read();
  if (have !== undefined) return Promise.resolve(have);
  if (!ownScript) return Promise.reject(new Error(`heed: cannot locate the ${what}; use the npm package's entry or pass your own`));
  const url = new URL(file, ownScript.src).href;
  return new Promise<T>((resolve, reject) => {
    const doc = win.document;
    const s = doc.createElement("script");
    s.src = scriptUrl(win as HeedGlobals, url);
    s.integrity = integrity;
    s.crossOrigin = "anonymous";
    s.async = true;
    if (ownScript.nonce) s.nonce = ownScript.nonce;
    s.onload = () => {
      const got = read();
      return got !== undefined ? resolve(got) : reject(new Error(`heed: the ${what} did not register`));
    };
    s.onerror = () => reject(new Error(`heed: could not load ${url}`));
    (doc.head || doc.documentElement).append(s);
  });
}
