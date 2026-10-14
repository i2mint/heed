/**
 * The screenshot rasterizer. Served as its own file (`heed-raster-<hash>.js`, built
 * from raster-entry.ts), loaded only when a visitor opens the widget with screenshots
 * on, so pages that never open it never pay for it; apps that bundle heed import it
 * lazily instead (lib.ts).
 *
 * It re-renders the visible part of the page from the DOM (modern-screenshot: SVG
 * foreignObject → canvas). That is a re-rendering, not true pixels: cross-origin
 * iframes, tainted canvases, videos and some CSS come out blank or approximate.
 *
 * Privacy, in two independent layers, both applied before the image leaves this
 * function:
 *   1. the cloned DOM is scrubbed before it is drawn: field values and masked
 *      elements' contents are removed, so they are never rendered. Each clone is
 *      paired with its live original (modern-screenshot clones depth-first: `filter`
 *      sees each original before it is cloned, `onCloneEachNode` each clone after its
 *      children), so what to mask is decided on the live page, where selectors,
 *      `type` and editability mean what they mean to the browser. If the pairing
 *      ever falls out of step, no screenshot is returned;
 *   2. black rectangles are painted over the final canvas at the positions the live
 *      page reports for every masked thing (the element, every box of its contents,
 *      including children positioned or overflowing outside it), so a selector that
 *      only matches in the live document, or content that escapes its box, is still
 *      covered.
 * Masked: password-like fields always (type password, or autocomplete
 * current-password / new-password / one-time-code / cc-*); with `maskInputs` (the
 * default) every field a visitor types into, selects, editable regions and every
 * iframe/embed; and whatever `maskSelector` matches (`[data-heed-mask]`). The widget
 * itself and `[data-heed-ignore]` are left out.
 *
 * Time: cloning and styling the DOM is main-thread work, and it grows with the page
 * (a 4000-element page took seconds on a slow machine). So it runs in slices of a
 * few milliseconds with a yield to the event loop between them (the clone is made
 * node by node, which is where the slices fall), the dialog stays responsive, and
 * the capture's `signal` (the timeout, or the visitor leaving) can end it at the
 * next yield instead of after it has finished. Because the page can change while
 * the capture yields, what to mask is decided when the live node is first seen, and
 * the black boxes are measured both before and after the render.
 */
import { createContext, destroyContext, domToCanvas } from "modern-screenshot";

export interface CaptureOptions {
  maskInputs: boolean;
  maskSelector: string;
  /** Largest encoded image to return. */
  maxBytes: number;
  /** Largest canvas, in pixels. */
  maxPixels: number;
  /** Skip pages with more elements than this: cloning them would freeze the page. */
  maxNodes: number;
  /** Ends the capture at its next yield; it then rejects. */
  signal?: AbortSignal;
}

export interface Raster {
  capture(win: Window, options: CaptureOptions): Promise<Blob | null>;
}

// Inputs that hold no typed text; every other input type (including unknown ones,
// which browsers treat as text) is masked.
const NOT_TYPED = new Set(["checkbox", "radio", "range", "color", "button", "submit", "reset", "image", "hidden"]);
const SECRET_AUTOCOMPLETE = /(^|\s)(current-password|new-password|one-time-code|cc-[a-z-]+)(\s|$)/;
const PAD = 3; // px around each masked box, for re-rendering drift

type Rect = { x: number; y: number; w: number; h: number };

const isEl = (n: Node | null | undefined): n is Element => !!n && n.nodeType === 1; // across iframe realms

function isSecretField(el: Element): boolean {
  if (el.localName !== "input") return false;
  const input = el as HTMLInputElement;
  return input.type === "password" || SECRET_AUTOCOMPLETE.test((input.getAttribute("autocomplete") || "").toLowerCase());
}

function isField(el: Element): boolean {
  const tag = el.localName;
  if (tag === "input") return !NOT_TYPED.has((el as HTMLInputElement).type);
  return (
    tag === "textarea" ||
    tag === "select" ||
    tag === "iframe" ||
    tag === "frame" ||
    tag === "embed" ||
    tag === "object" ||
    (el as HTMLElement).isContentEditable === true ||
    (el.hasAttribute("contenteditable") && el.getAttribute("contenteditable") !== "false")
  );
}

function matches(el: Element, selector: string): boolean {
  if (!selector) return false;
  try {
    return el.matches(selector);
  } catch {
    return false;
  }
}

/** Left out of the screenshot entirely: the widget itself and `[data-heed-ignore]`. */
const excluded = (el: Element) => el.localName === "heed-feedback" || el.hasAttribute("data-heed-ignore");

type Located = { el: Element; dx: number; dy: number };

/**
 * Every element that will be drawn, through open shadow roots and same-origin
 * iframes, with the offset that maps its client rects to the top viewport.
 */
function* allElements(root: Document | ShadowRoot, dx = 0, dy = 0): Generator<Located> {
  const doc = root.nodeType === 9 ? (root as Document) : (root.ownerDocument as Document);
  const walker = doc.createTreeWalker(root, NodeFilter.SHOW_ELEMENT, {
    acceptNode: (n) => (excluded(n as Element) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
  });
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const el = node as Element;
    yield { el, dx, dy };
    if (el.shadowRoot) yield* allElements(el.shadowRoot, dx, dy);
    if (el.localName === "iframe" || el.localName === "frame") {
      let inner: Document | null = null;
      try {
        inner = (el as HTMLIFrameElement).contentDocument;
      } catch {
        inner = null; // cross-origin: drawn blank anyway
      }
      if (inner) {
        const box = el.getBoundingClientRect();
        yield* allElements(inner, dx + box.left + el.clientLeft, dy + box.top + el.clientTop);
      }
    }
  }
}

function shouldMask(el: Element, o: CaptureOptions): boolean {
  if (isSecretField(el)) return true;
  if (o.maskInputs && isField(el)) return true;
  if (el.localName === "iframe" && (el as HTMLIFrameElement).contentDocument?.designMode === "on") return true;
  return matches(el, o.maskSelector);
}

/** Viewport rectangles to black out, measured on the live page. */
function maskRects(doc: Document, o: CaptureOptions, width: number, height: number): Rect[] {
  const rects: Rect[] = [];
  for (const { el, dx, dy } of allElements(doc)) {
    if (!shouldMask(el, o)) continue;
    const add = (r: DOMRect) => {
      const x = r.left + dx;
      const y = r.top + dy;
      if (r.width <= 0 && r.height <= 0) return;
      if (x + r.width < 0 || y + r.height < 0 || x > width || y > height) return;
      rects.push({ x: x - PAD, y: y - PAD, w: r.width + 2 * PAD, h: r.height + 2 * PAD });
    };
    for (const r of Array.from(el.getClientRects())) add(r);
    // Every box of its contents too: children positioned or overflowing outside it,
    // and elements with no box of their own (display: contents).
    const range = (el.ownerDocument as Document).createRange();
    range.selectNodeContents(el);
    for (const r of Array.from(range.getClientRects())) add(r);
    range.detach();
  }
  return rects;
}

const FORM_CONTENT = new Set(["select", "optgroup", "option", "textarea", "datalist"]);

/** A 1×1 black PNG: what a masked image, canvas or video becomes. */
const BLACK_PIXEL =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGAAAAAEAAH2FzhVAAAAAElFTkSuQmCC";

/**
 * Whether the browser draws this node at all. Light-DOM children of a closed shadow
 * host, unslotted content and display:none subtrees are invisible on the page but
 * would be drawn by a clone that cannot see the shadow root.
 */
function rendered(node: Node): boolean {
  // Form controls draw their options and text without boxes of their own.
  if (node.parentElement && FORM_CONTENT.has(node.parentElement.localName)) return true;
  if (isEl(node)) {
    const el = node as Element & { checkVisibility?: () => boolean };
    if (!el.checkVisibility || el.checkVisibility()) return true;
    const view = el.ownerDocument.defaultView;
    return !!view && view.getComputedStyle(el).display === "contents";
  }
  if (node.nodeType !== 3 || !(node as Text).data.trim()) return true;
  const range = (node.ownerDocument as Document).createRange();
  range.selectNodeContents(node);
  const drawn = range.getClientRects().length > 0;
  range.detach();
  return drawn;
}

/** Remove a masked element's content from its clone (layer 1). */
function blank(clone: Element, original: Element): void {
  const tag = clone.localName;
  if (tag === "input") {
    clone.removeAttribute("value");
    (clone as HTMLInputElement).value = "";
    clone.removeAttribute("placeholder");
  } else if (tag === "img") {
    // An image, or a canvas/video cloned as one: a black picture of the same size.
    const box = original.getBoundingClientRect();
    for (const a of ["srcset", "sizes", "alt"]) clone.removeAttribute(a);
    clone.setAttribute("src", BLACK_PIXEL);
    (clone as HTMLElement).style?.setProperty("width", `${box.width}px`, "important");
    (clone as HTMLElement).style?.setProperty("height", `${box.height}px`, "important");
    (clone as HTMLElement).style?.setProperty("object-fit", "fill", "important");
  } else {
    clone.replaceChildren();
    for (const a of ["href", "xlink:href", "src", "srcdoc", "data", "poster"]) clone.removeAttribute(a);
  }
  // Pseudo-elements are attached through generated classes: drop them all.
  clone.removeAttribute("class");
  const style = (clone as HTMLElement).style;
  style?.setProperty("background", "#000", "important");
  style?.setProperty("overflow", "hidden", "important");
}

/** A clone modern-screenshot may legitimately make of `original`. */
function pairs(original: Element, clone: Element): boolean {
  const a = original.localName;
  const b = clone.localName;
  return a === b || (a === "iframe" && b === "html") || ((a === "canvas" || a === "video") && (b === "img" || b === "canvas"));
}

/**
 * Pairs clones with their originals as modern-screenshot clones (see layer 1 above),
 * and blanks the clones of masked originals.
 */
function pairingScrubber(o: CaptureOptions, slice: () => Promise<void>) {
  // Whether to mask is decided when the live element is first seen (`filter`), not when
  // its clone is finished: the page may change in between, now that capture yields.
  const pending: { original: Element; mask: boolean }[] = [];
  let last: Node | null = null;
  let broken = false;
  return {
    filter(node: Node): boolean {
      if (!rendered(node)) return false;
      if (!isEl(node)) return true;
      if (excluded(node)) return false;
      pending.push({ original: node, mask: shouldMask(node, o) });
      return true;
    },
    async onCloneEachNode(clone: Node): Promise<void> {
      // A same-origin iframe is cloned as its document: that clone is reported twice.
      if (isEl(clone) && clone !== last) {
        last = clone;
        const entry = pending.pop();
        if (entry) {
          if (!pairs(entry.original, clone)) broken = true;
          else if (entry.mask) blank(clone, entry.original);
        } // else: the root
      }
      await slice();
    },
    get broken() {
      return broken || pending.length > 0;
    },
  };
}

/** Keep `position: fixed` elements in place: the scrolled content around them is shifted. */
function pinFixed(root: Element, scroll: { x: number; y: number }): void {
  for (const el of Array.from(root.querySelectorAll<HTMLElement>("*"))) {
    if (el.style?.position === "fixed" && !el.parentElement?.closest('[style*="position: fixed"]')) {
      const t = el.style.transform;
      el.style.transform = `translate(${scroll.x}px, ${scroll.y}px)${t && t !== "none" ? ` ${t}` : ""}`;
    }
  }
}

/** Longest stretch of capture work before the page gets the thread back. */
const SLICE_MS = 12;

/**
 * Give the thread back to the page: its input, timers and paints run before we go on.
 * A message, not `scheduler.yield()` (its continuation runs ahead of rendering, which
 * measured at one frame in ten) and not `setTimeout` (clamped to 4 ms, so 25% slower).
 */
function yieldToPage(): Promise<void> {
  return new Promise((resolve) => {
    const { port1, port2 } = new MessageChannel();
    port1.onmessage = () => {
      port1.close();
      resolve();
    };
    port2.postMessage(null);
  });
}

const cancelled = () => new Error("heed: screenshot cancelled");

/**
 * `slice()` yields to the page once the current slice has run for SLICE_MS (`slice(true)`:
 * now); either way it throws once `signal` has fired.
 */
function slicer(signal?: AbortSignal) {
  let started = performance.now();
  return async (force = false): Promise<void> => {
    if (signal?.aborted) throw cancelled();
    if (!force && performance.now() - started < SLICE_MS) return;
    await yieldToPage();
    if (signal?.aborted) throw cancelled();
    started = performance.now();
  };
}

/**
 * The hidden frame modern-screenshot reads the browsers' default styles from. It makes
 * one on first use and reads its document at once, which only works because nothing
 * yields before the first read; we do yield, so we hand it one that has loaded.
 */
async function loadedSandbox(doc: Document, signal?: AbortSignal): Promise<HTMLIFrameElement> {
  const frame = doc.createElement("iframe");
  frame.width = frame.height = "0";
  frame.style.visibility = "hidden";
  frame.style.position = "fixed";
  const loaded = new Promise<boolean>((resolve) => {
    frame.addEventListener("load", () => resolve(true), { once: true });
    setTimeout(() => resolve(false), 2000);
  });
  frame.srcdoc = '<!DOCTYPE html><meta charset="UTF-8"><title></title><body>';
  doc.body.appendChild(frame);
  const ready = await loaded;
  if (signal?.aborted) throw cancelled();
  if (!ready || !frame.contentDocument?.body) {
    frame.remove();
    throw new Error("heed: the screenshot's style frame did not load; no screenshot");
  }
  return frame;
}

const toBlob = (canvas: HTMLCanvasElement, type: string, quality?: number) =>
  new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, type, quality));

/** True when nothing was drawn (e.g. a CSP that blocks the data: image it renders through). */
function isBlank(canvas: HTMLCanvasElement): boolean {
  const probe = canvas.ownerDocument.createElement("canvas");
  probe.width = probe.height = 16;
  const ctx = probe.getContext("2d");
  if (!ctx) return false;
  ctx.drawImage(canvas, 0, 0, 16, 16);
  const data = ctx.getImageData(0, 0, 16, 16).data;
  for (let i = 3; i < data.length; i += 4) if (data[i] !== 0) return false;
  return true;
}

/** Render the visible page, masked, as a PNG or JPEG under `o.maxBytes` (null if none can be taken). */
export async function capture(win: Window, o: CaptureOptions): Promise<Blob | null> {
  const doc = win.document;
  const slice = slicer(o.signal);
  let count = 0;
  for (const _ of allElements(doc)) {
    if (++count > o.maxNodes) {
      console.warn(`heed: no screenshot: the page has over ${o.maxNodes} elements (screenshotMaxNodes)`);
      return null;
    }
  }
  const width = win.innerWidth;
  const height = win.innerHeight;
  const scroll = { x: win.scrollX, y: win.scrollY };
  const scale = Math.min(win.devicePixelRatio || 1, 2, Math.sqrt(Math.min(o.maxPixels, 8_000_000) / (width * height)));
  const before = maskRects(doc, o, width, height);
  // The page's own background (set on body or html), so dark pages stay readable.
  const background =
    [doc.body, doc.documentElement]
      .map((el) => (el ? win.getComputedStyle(el).backgroundColor : ""))
      .find((c) => c && c !== "transparent" && !/rgba\([^)]*,\s*0\)$/.test(c)) ?? "#fff";
  const scrubber = pairingScrubber(o, slice);
  const context = await createContext(doc.documentElement, {
    width,
    height,
    scale,
    backgroundColor: background,
    filter: (node) => scrubber.filter(node),
    onCloneEachNode: (clone) => scrubber.onCloneEachNode(clone),
    onCloneNode: async (root) => {
      if (scrubber.broken) throw new Error("heed: lost track of the page while masking; no screenshot");
      if (isEl(root)) pinFixed(root, scroll);
      await slice(true);
    },
    // After the fonts and images are embedded, before the (single, synchronous) serialization.
    onEmbedNode: () => slice(true),
    timeout: 5000,
    // Draw scrolled containers (the document included) as scrolled, so the visible
    // part is drawn and layer 2's boxes land where they should.
    features: { restoreScrollPosition: true },
    autoDestruct: true,
  });
  let canvas: HTMLCanvasElement;
  try {
    context.sandbox = await loadedSandbox(doc, o.signal);
    canvas = await domToCanvas(context);
  } finally {
    destroyContext(context); // also when cancelled or failed: it removes the style frame from the page
  }
  await slice(true);
  const ctx = canvas.getContext("2d");
  if (!ctx || isBlank(canvas)) return null; // nothing drawn (e.g. a CSP without img-src data:)
  // Layer 2: black boxes where the live page has masked content: where it was when the
  // capture began and where it is now, since the page may have moved while we yielded.
  const rects = [...before, ...maskRects(doc, o, width, height)];
  const k = canvas.width / width;
  ctx.fillStyle = "#000";
  for (const r of rects) ctx.fillRect(r.x * k, r.y * k, r.w * k, r.h * k);
  // PNG first; JPEG when that is over the cap; then a half-size JPEG.
  for (const [type, quality] of [["image/png"], ["image/jpeg", 0.9], ["image/jpeg", 0.75]] as const) {
    const blob = await toBlob(canvas, type, quality);
    if (blob && blob.size <= o.maxBytes) return blob;
  }
  const small = doc.createElement("canvas");
  small.width = Math.max(1, Math.floor(canvas.width / 2));
  small.height = Math.max(1, Math.floor(canvas.height / 2));
  small.getContext("2d")?.drawImage(canvas, 0, 0, small.width, small.height);
  const blob = await toBlob(small, "image/jpeg", 0.75);
  return blob && blob.size <= o.maxBytes ? blob : null;
}

