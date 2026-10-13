/**
 * Taking the screenshot from the widget bundle: load the rasterizer on first use and
 * bound the whole capture by a deadline, so a slow or failing capture never blocks a
 * report (it is sent without the screenshot instead).
 *
 * The rasterizer file is located next to this bundle's own `<script>` and loaded with
 * the SRI hash baked in at build time (the same chain as loader → bundle). Code that
 * bundles heed itself (no `<script>` to locate) passes its own `takeScreenshot`.
 */
import { CONTRACT } from "./contract.gen.js";
import type { ScreenshotTaker } from "./options.js";
import type { CaptureOptions, Raster } from "./raster.js";

declare const __HEED_RASTER__: string;
declare const __HEED_RASTER_INTEGRITY__: string;

type TT = { createPolicy(n: string, p: object): { createScriptURL(u: string): unknown } };
type HeedGlobals = Window & {
  __heedRaster?: Raster;
  __heedTT?: { createScriptURL(u: string): unknown } | null;
  trustedTypes?: TT;
};

/** This bundle's own `<script>`; null when an app bundled heed into its own code. */
const ownScript = (() => {
  const s = typeof document !== "undefined" ? (document.currentScript as HTMLScriptElement | null) : null;
  return s && /\/heed-widget(-[0-9a-f]{10})?\.js([?#]|$)/.test(s.src) ? s : null;
})();
let raster: Promise<Raster> | null = null;
let csp: boolean | undefined;

/**
 * Whether this page's CSP lets the rasterizer work: it builds `<style>` elements and
 * markup strings, so it needs inline styles and no Trusted Types enforcement.
 * Probed once, before anything is downloaded; the style probe can itself produce one
 * CSP violation report on a strict page.
 */
export function screenshotsAllowed(doc: Document): boolean {
  if (csp !== undefined) return csp;
  csp = true;
  try {
    doc.createElement("template").innerHTML = "<b></b>";
  } catch {
    csp = false; // Trusted Types enforced
  }
  if (csp) {
    const style = doc.createElement("style");
    style.textContent = "heed-probe{}";
    (doc.head || doc.documentElement).append(style);
    csp = !!style.sheet;
    style.remove();
  }
  if (!csp) console.warn("heed: this page's Content-Security-Policy blocks screenshots (they need inline styles and no Trusted Types enforcement)");
  return csp;
}

/** Load `heed-raster-<hash>.js` once, next to this bundle, pinned by SRI. */
export function loadRaster(win: Window = window): Promise<Raster> {
  const w = win as HeedGlobals;
  if (w.__heedRaster) return Promise.resolve(w.__heedRaster);
  if (raster) return raster;
  if (!ownScript) return Promise.reject(new Error("heed: cannot locate the rasterizer; pass takeScreenshot"));
  const url = new URL(__HEED_RASTER__, ownScript.src).href;
  raster = new Promise<Raster>((resolve, reject) => {
    const doc = win.document;
    const s = doc.createElement("script");
    s.src = scriptUrl(w, url);
    s.integrity = __HEED_RASTER_INTEGRITY__;
    s.crossOrigin = "anonymous";
    s.async = true;
    if (ownScript.nonce) s.nonce = ownScript.nonce;
    s.onload = () => (w.__heedRaster ? resolve(w.__heedRaster) : reject(new Error("heed: rasterizer did not register")));
    s.onerror = () => reject(new Error(`heed: could not load ${url}`));
    (doc.head || doc.documentElement).append(s);
  });
  raster.catch(() => (raster = null)); // allow a retry on the next open
  return raster;
}

/** A Trusted Types-safe script URL: through the loader's `heed` policy, or our own. */
function scriptUrl(w: HeedGlobals, url: string): string {
  if (w.__heedTT === undefined) {
    const dir = new URL(".", url).href;
    const own = (u: string) => (u.startsWith(dir) && /^heed-(widget|raster)-[0-9a-f]{10}\.js$/.test(u.slice(dir.length)) ? u : "");
    try {
      w.__heedTT = w.trustedTypes?.createPolicy("heed", { createScriptURL: own }) ?? null;
    } catch {
      w.__heedTT = null; // the page's CSP does not allow the policy; screenshotsAllowed said no anyway
    }
  }
  return (w.__heedTT ? w.__heedTT.createScriptURL(url) : url) as string;
}

/** The built-in strategy for the served bundle: the rasterizer file, loaded on first use. */
export const builtinScreenshot: ScreenshotTaker = async (win, options) =>
  screenshotsAllowed(win.document) ? (await loadRaster(win)).capture(win, options) : null;

let defaultScreenshot: ScreenshotTaker = builtinScreenshot;

/** The strategy used when an element has no `takeScreenshot` of its own. */
export const screenshotStrategy = (): ScreenshotTaker => defaultScreenshot;

/** Replace the default strategy (lib.ts does, for apps that bundle heed). */
export function setScreenshotStrategy(take: ScreenshotTaker): void {
  defaultScreenshot = take;
}

/**
 * The widget stays well under the server's cap: the server re-encodes JPEGs at a
 * higher quality, which can make them grow.
 */
export const MAX_SCREENSHOT_BYTES = Math.floor(CONTRACT.limits.screenshot_bytes * 0.6);

/** Run `take` within `timeoutMs`; any failure or timeout gives null, never an exception. */
export async function captureWithin(
  take: ScreenshotTaker,
  win: Window,
  options: Omit<CaptureOptions, "maxBytes" | "maxPixels">,
  timeoutMs: number,
): Promise<Blob | null> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const deadline = new Promise<null>((resolve) => (timer = setTimeout(() => resolve(null), timeoutMs)));
  try {
    const shot = take(win, {
      ...options,
      maxBytes: MAX_SCREENSHOT_BYTES,
      maxPixels: CONTRACT.limits.screenshot_pixels,
    }).catch((e) => {
      console.warn("heed: no screenshot", e);
      return null;
    });
    const blob = await Promise.race([shot, deadline]);
    return blob instanceof Blob && blob.size > 0 && blob.size <= MAX_SCREENSHOT_BYTES ? blob : null;
  } catch (e) {
    console.warn("heed: no screenshot", e);
    return null;
  } finally {
    clearTimeout(timer);
  }
}
