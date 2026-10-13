/**
 * `<HeedFeedback>`: a thin React wrapper over the `<heed-feedback>` element, for apps
 * that bundle heed (`import { HeedFeedback } from "heed-widget/react"`).
 *
 * It is not a second implementation: it renders the custom element, passes every
 * prop through `configure()` (so function props such as `botToken` work in React 18
 * and 19 alike), and turns the element's events into callbacks.
 *
 *   <HeedFeedback endpoint="/heed/report" label="Feedback"
 *                 onSubmitted={(r) => console.log(r.id)} />
 *
 * A ref gives `open()` and `close()`, for opening it from your own menu item.
 * `id`, `title`, `data-*` and `aria-*` props go onto the element itself.
 *
 * Safe to import during server-side rendering: the element is only defined in a
 * browser, and the package's entries are marked "use client".
 */
import { createElement, forwardRef, useEffect, useImperativeHandle, useRef, type CSSProperties } from "react";
import "./lib.js";
import type { HeedFeedbackElement } from "./element.js";
import { TAG } from "./element.js";
import { type HeedOptions, defaultOptions } from "./options.js";

export interface HeedSubmitted {
  id: string;
  status: "received" | "held";
  url: string | null;
}

/** Attributes forwarded to the element itself (a test id, a label…). */
type ElementAttributes = { id?: string; title?: string } & { [K in `data-${string}` | `aria-${string}`]?: string };

export interface HeedFeedbackProps extends Partial<HeedOptions>, ElementAttributes {
  onOpen?: () => void;
  onClose?: () => void;
  onSubmitted?: (report: HeedSubmitted) => void;
  onError?: (error: { kind: string }) => void;
  className?: string;
  /** Inline style, e.g. theming custom properties: `{ "--heed-accent": "#0a7" }`. */
  style?: CSSProperties | (CSSProperties & Record<`--${string}`, string>);
}

export interface HeedFeedbackHandle {
  open(): void;
  close(): void;
  readonly element: HeedFeedbackElement | null;
}

const EVENTS = { onOpen: "heed:open", onClose: "heed:close", onSubmitted: "heed:submitted", onError: "heed:error" } as const;

export const HeedFeedback = forwardRef<HeedFeedbackHandle, HeedFeedbackProps>(function HeedFeedback(props, ref) {
  const { onOpen, onClose, onSubmitted, onError, className, style, ...rest } = props;
  const { id, title, ...others } = rest;
  const attributes: Record<string, unknown> = {};
  const options: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(others)) {
    if (key.startsWith("data-") || key.startsWith("aria-")) attributes[key] = value;
    else options[key] = value;
  }
  const el = useRef<HeedFeedbackElement>(null);
  const previous = useRef<Record<string, unknown>>({});
  const handlers = useRef({ onOpen, onClose, onSubmitted, onError });
  handlers.current = { onOpen, onClose, onSubmitted, onError };

  useImperativeHandle(
    ref,
    () => ({
      open: () => el.current?.open(),
      close: () => el.current?.close(),
      get element() {
        return el.current;
      },
    }),
    [],
  );

  // Every render: pass changed props on, and reset options whose prop was removed.
  useEffect(() => {
    const element = el.current;
    if (!element) return;
    const given = Object.fromEntries(Object.entries(options).filter(([, v]) => v !== undefined));
    const before = previous.current;
    const defaults = defaultOptions() as unknown as Record<string, unknown>;
    const changes: Record<string, unknown> = {};
    for (const key of Object.keys(before)) if (!(key in given)) changes[key] = defaults[key];
    for (const [key, value] of Object.entries(given)) if (!(key in before) || before[key] !== value) changes[key] = value;
    previous.current = given;
    if (Object.keys(changes).length) element.configure(changes);
    // Kept right here: React 18 does not map className onto custom elements, and
    // React 19 assigns a removed id/title as the string "undefined".
    for (const [name, value] of [["class", className], ["id", id], ["title", title]] as const) {
      if (value) element.setAttribute(name, value);
      else element.removeAttribute(name);
    }
  });

  useEffect(() => {
    const element = el.current;
    if (!element) return;
    const listeners = Object.entries(EVENTS).map(([prop, type]) => {
      const listener = (e: Event) => {
        const handler = handlers.current[prop as keyof typeof EVENTS] as ((detail: never) => void) | undefined;
        handler?.((e as CustomEvent).detail as never);
      };
      element.addEventListener(type, listener);
      return () => element.removeEventListener(type, listener);
    });
    return () => listeners.forEach((off) => off());
  }, []);

  // id/title also go through React when set, so server-side rendering includes them.
  return createElement(TAG, { ...attributes, ...(id ? { id } : {}), ...(title ? { title } : {}), ref: el, style });
});
