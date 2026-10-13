/**
 * The screenshot rasterizer: its own file (`heed-raster-<hash>.js`), loaded only when
 * a visitor opens the widget with screenshots on, so pages that never open it never
 * pay for it.
 *
 * It re-renders the visible part of the page from the DOM (modern-screenshot: SVG
 * foreignObject → canvas). That is a re-rendering, not true pixels: cross-origin
 * iframes, tainted canvases, videos and some CSS come out blank or approximate.
 *
 * It needs a page that allows inline styles and does not enforce Trusted Types
 * (modern-screenshot builds `<style>` elements and markup strings); on stricter pages
 * `capturable` says no after one cheap probe, and the report is sent without a
 * screenshot.
 *
 * Privacy: masking happens on the cloned DOM, before anything is drawn, so masked
 * content never reaches the image. Password fields are always masked; with
 * `maskInputs` (the default) every text field shows bullets and selects and
 * contenteditable regions are blacked out; elements matching `maskSelector`
 * (`[data-heed-mask]` by default) are blacked out; elements marked
 * `data-heed-ignore` and the widget itself are left out entirely.
 */
import { domToCanvas } from "modern-screenshot";

export interface CaptureOptions {
  maskInputs: boolean;
  maskSelector: string;
  /** Largest encoded image to return (the server's cap). */
  maxBytes: number;
  /** Largest canvas, in pixels (the server refuses larger images). */
  maxPixels: number;
}

export interface Raster {
  capture(win: Window, options: CaptureOptions): Promise<Blob | null>;
}

const TEXT_INPUTS = new Set(["", "text", "search", "email", "tel", "url", "number", "date", "datetime-local", "month", "week", "time", "password"]);
const BLOCK = (el: HTMLElement) => {
  el.style.setProperty("background", "#000", "important");
  el.style.setProperty("filter", "brightness(0)", "important");
};
const bullets = (s: string) => s.replace(/\S/g, "•").slice(0, 200);

function mask(root: Element, o: CaptureOptions, scroll: { x: number; y: number }): void {
  for (const el of Array.from(root.querySelectorAll<HTMLElement>("*"))) {
    if (el.style.position === "fixed" && !el.parentElement?.closest('[style*="position: fixed"]')) {
      // The root is shifted by the scroll offset; fixed elements must stay put.
      const t = el.style.transform;
      el.style.transform = `translate(${scroll.x}px, ${scroll.y}px)${t && t !== "none" ? ` ${t}` : ""}`;
    }
    const tag = el.tagName;
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (tag === "INPUT" && (type === "password" || (o.maskInputs && TEXT_INPUTS.has(type)))) {
      el.setAttribute("value", bullets(el.getAttribute("value") || ""));
    } else if (tag === "TEXTAREA" && o.maskInputs) {
      el.textContent = bullets(el.textContent || "");
    } else if (
      (o.maskInputs && (tag === "SELECT" || (tag === "INPUT" && type === "file") || el.isContentEditable || el.matches('[contenteditable]:not([contenteditable="false"])'))) ||
      (o.maskSelector && safeMatches(el, o.maskSelector))
    ) {
      BLOCK(el);
    }
  }
}

function safeMatches(el: Element, selector: string): boolean {
  try {
    return el.matches(selector);
  } catch {
    return false;
  }
}

const toBlob = (canvas: HTMLCanvasElement, type: string, quality?: number) =>
  new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, type, quality));

let probed: boolean | undefined;

/** Whether this page's CSP lets the rasterizer work (probed once). */
function capturable(doc: Document): boolean {
  if (probed !== undefined) return probed;
  const style = doc.createElement("style");
  style.textContent = "heed-probe{}";
  (doc.head || doc.documentElement).append(style);
  probed = !!style.sheet;
  style.remove();
  if (probed) {
    try {
      doc.createElement("template").innerHTML = "<b></b>";
    } catch {
      probed = false; // Trusted Types enforced
    }
  }
  if (!probed) console.warn("heed: this page's Content-Security-Policy blocks screenshots (they need inline styles and no Trusted Types enforcement)");
  return probed;
}

async function capture(win: Window, o: CaptureOptions): Promise<Blob | null> {
  const doc = win.document;
  if (!capturable(doc)) return null;
  const width = win.innerWidth;
  const height = win.innerHeight;
  const scroll = { x: win.scrollX, y: win.scrollY };
  const pixelCap = Math.min(o.maxPixels, 8_000_000);
  const scale = Math.min(win.devicePixelRatio || 1, 2, Math.sqrt(pixelCap / (width * height)));
  const canvas = await domToCanvas(doc.documentElement, {
    width,
    height,
    scale,
    style: { transform: `translate(${-scroll.x}px, ${-scroll.y}px)` },
    filter: (node) =>
      !(node instanceof Element) || !(node.localName === "heed-feedback" || node.hasAttribute("data-heed-ignore")),
    onCloneNode: (root) => {
      if (root instanceof Element) mask(root, o, scroll);
    },
    timeout: 5000,
  });
  // PNG first; JPEG when that is over the cap; then a half-size JPEG.
  for (const [type, quality] of [["image/png"], ["image/jpeg", 0.85], ["image/jpeg", 0.7]] as const) {
    const blob = await toBlob(canvas, type, quality);
    if (blob && blob.size <= o.maxBytes) return blob;
  }
  const small = doc.createElement("canvas");
  small.width = Math.max(1, Math.floor(canvas.width / 2));
  small.height = Math.max(1, Math.floor(canvas.height / 2));
  small.getContext("2d")?.drawImage(canvas, 0, 0, small.width, small.height);
  const blob = await toBlob(small, "image/jpeg", 0.7);
  return blob && blob.size <= o.maxBytes ? blob : null;
}

(window as unknown as { __heedRaster?: Raster }).__heedRaster = { capture };
