/**
 * Taking the screenshot from the widget bundle: load the rasterizer on first use and
 * bound the whole capture by a deadline, so a slow or failing capture never blocks a
 * report (it is sent without the screenshot instead).
 *
 * The rasterizer file is located next to this bundle's own `<script>` and loaded with
 * the SRI hash baked in at build time (the same chain as loader → bundle). Code that
 * bundles heed itself (no `<script>` to locate) passes its own `takeScreenshot`.
 */
import { CONTRACT } from "./contract.gen";
import type { ScreenshotTaker } from "./options";
import type { CaptureOptions, Raster } from "./raster";

declare const __HEED_RASTER__: string;
declare const __HEED_RASTER_INTEGRITY__: string;

type TT = { createPolicy(n: string, p: object): { createScriptURL(u: string): unknown } };
type HeedGlobals = Window & {
  __heedRaster?: Raster;
  __heedTT?: { p: { createScriptURL(u: string): unknown } | null; a: Set<string> };
  trustedTypes?: TT;
};

const ownScript = typeof document !== "undefined" ? (document.currentScript as HTMLScriptElement | null) : null;
let raster: Promise<Raster> | null = null;

/** Load `heed-raster-<hash>.js` once (null when this bundle has no script URL to resolve it against). */
export function loadRaster(win: Window = window): Promise<Raster> {
  const w = win as HeedGlobals;
  if (w.__heedRaster) return Promise.resolve(w.__heedRaster);
  if (raster) return raster;
  if (!ownScript?.src) return Promise.reject(new Error("heed: cannot locate the rasterizer (no bundle script URL)"));
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

/** A Trusted Types-safe script URL: through the loader's `heed` policy, or one of our own. */
function scriptUrl(w: HeedGlobals, url: string): string {
  let tt = w.__heedTT;
  if (!tt) {
    const allowed = new Set<string>();
    let policy = null;
    try {
      policy = w.trustedTypes?.createPolicy("heed", { createScriptURL: (u: string) => (allowed.has(u) ? u : "") }) ?? null;
    } catch {
      // the page's CSP does not allow the policy; the assignment below then fails
    }
    tt = w.__heedTT = { p: policy, a: allowed };
  }
  tt.a.add(url);
  return (tt.p ? tt.p.createScriptURL(url) : url) as string;
}

/** The built-in strategy: the lazily loaded rasterizer. */
export const builtinScreenshot: ScreenshotTaker = async (win, options) => (await loadRaster(win)).capture(win, options);

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
      maxBytes: CONTRACT.limits.screenshot_bytes,
      maxPixels: CONTRACT.limits.screenshot_pixels,
    }).catch((e) => {
      console.warn("heed: no screenshot", e);
      return null;
    });
    const blob = await Promise.race([shot, deadline]);
    return blob instanceof Blob && blob.size > 0 && blob.size <= CONTRACT.limits.screenshot_bytes ? blob : null;
  } catch (e) {
    console.warn("heed: no screenshot", e);
    return null;
  } finally {
    clearTimeout(timer);
  }
}
