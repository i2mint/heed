/**
 * The screenshot rasterizer: its own file (`heed-raster-<hash>.js`), loaded only when
 * a visitor opens the widget with screenshots on, so pages that never open it never
 * pay for it.
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
 */
import { domToCanvas } from "modern-screenshot";

export interface CaptureOptions {
  maskInputs: boolean;
  maskSelector: string;
  /** Largest encoded image to return. */
  maxBytes: number;
  /** Largest canvas, in pixels. */
  maxPixels: number;
  /** Skip pages with more elements than this: cloning them would freeze the page. */
  maxNodes: number;
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

/** Every element of the document, through open shadow roots. */
function* allElements(root: Document | ShadowRoot): Generator<Element> {
  const walker = (root instanceof Document ? root : root.ownerDocument).createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const el = node as Element;
    yield el;
    if (el.shadowRoot) yield* allElements(el.shadowRoot);
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
  const add = (r: DOMRect) => {
    if (r.width <= 0 && r.height <= 0) return;
    if (r.right < 0 || r.bottom < 0 || r.left > width || r.top > height) return;
    rects.push({ x: r.left - PAD, y: r.top - PAD, w: r.width + 2 * PAD, h: r.height + 2 * PAD });
  };
  for (const el of allElements(doc)) {
    if (!shouldMask(el, o)) continue;
    // (Same-origin iframes are covered as a whole when masked; their insides are
    // reached by layer 1.)
    for (const r of Array.from(el.getClientRects())) add(r);
    // Every box of its contents too: children positioned or overflowing outside it,
    // and elements with no box of their own (display: contents).
    const range = doc.createRange();
    range.selectNodeContents(el);
    for (const r of Array.from(range.getClientRects())) add(r);
    range.detach();
  }
  return rects;
}

/** Remove a masked element's content from its clone (layer 1). */
function blank(clone: Element): void {
  if (clone.localName === "input") {
    clone.removeAttribute("value");
    (clone as HTMLInputElement).value = "";
    clone.removeAttribute("placeholder");
  } else {
    clone.replaceChildren();
  }
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
function pairingScrubber(o: CaptureOptions, excluded: (el: Element) => boolean) {
  const pending: Element[] = [];
  let last: Node | null = null;
  let broken = false;
  return {
    filter(node: Node): boolean {
      if (!isEl(node)) return true;
      if (excluded(node)) return false;
      pending.push(node);
      return true;
    },
    onCloneEachNode(clone: Node): void {
      // A same-origin iframe is cloned as its document: that clone is reported twice.
      if (!isEl(clone) || clone === last) return;
      last = clone;
      const original = pending.pop();
      if (!original) return; // the root
      if (!pairs(original, clone)) broken = true;
      else if (shouldMask(original, o)) blank(clone);
    },
    get broken() {
      return broken || pending.length > 0;
    },
  };
}

/** Keep `position: fixed` elements in place: the clone's root is shifted by the scroll. */
function pinFixed(root: Element, scroll: { x: number; y: number }): void {
  for (const el of Array.from(root.querySelectorAll<HTMLElement>("*"))) {
    if (el.style?.position === "fixed" && !el.parentElement?.closest('[style*="position: fixed"]')) {
      const t = el.style.transform;
      el.style.transform = `translate(${scroll.x}px, ${scroll.y}px)${t && t !== "none" ? ` ${t}` : ""}`;
    }
  }
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

async function capture(win: Window, o: CaptureOptions): Promise<Blob | null> {
  const doc = win.document;
  const count = doc.getElementsByTagName("*").length;
  if (count > o.maxNodes) {
    console.warn(`heed: no screenshot: the page has ${count} elements (screenshotMaxNodes is ${o.maxNodes})`);
    return null;
  }
  const width = win.innerWidth;
  const height = win.innerHeight;
  const scroll = { x: win.scrollX, y: win.scrollY };
  const scale = Math.min(win.devicePixelRatio || 1, 2, Math.sqrt(Math.min(o.maxPixels, 8_000_000) / (width * height)));
  const rects = maskRects(doc, o, width, height);
  const scrubber = pairingScrubber(o, (el) => el.localName === "heed-feedback" || el.hasAttribute("data-heed-ignore"));
  const canvas = await domToCanvas(doc.documentElement, {
    width,
    height,
    scale,
    style: { transform: `translate(${-scroll.x}px, ${-scroll.y}px)` },
    filter: (node) => scrubber.filter(node),
    onCloneEachNode: (clone) => scrubber.onCloneEachNode(clone),
    onCloneNode: (root) => {
      if (scrubber.broken) throw new Error("heed: lost track of the page while masking; no screenshot");
      if (isEl(root)) pinFixed(root, scroll);
    },
    timeout: 5000,
  });
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  // Layer 2: black boxes where the live page has masked content.
  const k = canvas.width / width;
  ctx.fillStyle = "#000";
  for (const r of rects) ctx.fillRect(r.x * k, r.y * k, r.w * k, r.h * k);
  if (isBlank(canvas)) return null;
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

(window as unknown as { __heedRaster?: Raster }).__heedRaster = { capture };
