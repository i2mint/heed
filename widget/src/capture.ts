/**
 * Taking the screenshot from the widget bundle: load the rasterizer on first use and
 * bound the whole capture by a deadline, so a slow or failing capture never blocks a
 * report (it is sent without the screenshot instead).
 *
 * The rasterizer file is loaded as a sibling of the bundle (sibling.ts). Code that
 * bundles heed itself (no `<script>` to locate) passes its own `takeScreenshot`.
 */
import { CONTRACT } from "./contract.gen.js";
import type { ScreenshotTaker } from "./options.js";
import { loadSibling } from "./sibling.js";
import type { CaptureOptions, Raster } from "./raster.js";

declare const __HEED_RASTER__: string;
declare const __HEED_RASTER_INTEGRITY__: string;

type HeedGlobals = Window & { __heedRaster?: Raster };

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
  if (raster) return raster;
  raster = loadSibling(win, __HEED_RASTER__, __HEED_RASTER_INTEGRITY__, () => (win as HeedGlobals).__heedRaster, "rasterizer");
  raster.catch(() => (raster = null)); // allow a retry on the next open
  return raster;
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
  options: Omit<CaptureOptions, "maxBytes" | "maxPixels" | "signal">,
  timeoutMs: number,
  cancel?: AbortSignal,
): Promise<Blob | null> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  // Fired when the capture ends either way, so a rasterizer that yields (raster.ts)
  // stops working for a screenshot nobody is waiting for any more.
  if (cancel?.aborted) return null;
  const stop = new AbortController();
  const onCancel = () => stop.abort();
  cancel?.addEventListener("abort", onCancel, { once: true });
  const deadline = new Promise<null>((resolve) => {
    timer = setTimeout(() => resolve(null), timeoutMs);
    stop.signal.addEventListener("abort", () => resolve(null)); // cancelled: nobody is waiting
  });
  try {
    const shot = take(win, {
      ...options,
      maxBytes: MAX_SCREENSHOT_BYTES,
      maxPixels: CONTRACT.limits.screenshot_pixels,
      signal: stop.signal,
    }).catch((e) => {
      if (!stop.signal.aborted) console.warn("heed: no screenshot", e);
      return null;
    });
    const blob = await Promise.race([shot, deadline]);
    return blob instanceof Blob && blob.size > 0 && blob.size <= MAX_SCREENSHOT_BYTES ? blob : null;
  } catch (e) {
    console.warn("heed: no screenshot", e);
    return null;
  } finally {
    clearTimeout(timer);
    cancel?.removeEventListener("abort", onCancel);
    stop.abort();
  }
}
