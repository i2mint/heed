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
 */
import { createElement, forwardRef, useEffect, useImperativeHandle, useRef, type CSSProperties } from "react";
import "./lib";
import type { HeedFeedbackElement } from "./element";
import { TAG } from "./element";
import { type HeedOptions, defaultOptions } from "./options";

export interface HeedSubmitted {
  id: string;
  status: "received" | "held";
  url: string | null;
}

export interface HeedFeedbackProps extends Partial<HeedOptions> {
  onOpen?: () => void;
  onClose?: () => void;
  onSubmitted?: (report: HeedSubmitted) => void;
  onError?: (error: { kind: string }) => void;
  className?: string;
  /** Inline style, e.g. theming custom properties: `{ "--heed-accent": "#0a7" }`. */
  style?: CSSProperties & Record<`--${string}`, string>;
}

export interface HeedFeedbackHandle {
  open(): void;
  close(): void;
  readonly element: HeedFeedbackElement | null;
}

const EVENTS = { onOpen: "heed:open", onClose: "heed:close", onSubmitted: "heed:submitted", onError: "heed:error" } as const;

export const HeedFeedback = forwardRef<HeedFeedbackHandle, HeedFeedbackProps>(function HeedFeedback(props, ref) {
  const { onOpen, onClose, onSubmitted, onError, className, style, ...options } = props;
  const el = useRef<HeedFeedbackElement>(null);
  const previous = useRef<string[]>([]);
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

  // Every render: pass the props on, and reset options whose prop was removed.
  useEffect(() => {
    const element = el.current;
    if (!element) return;
    const given = Object.fromEntries(Object.entries(options).filter(([, v]) => v !== undefined));
    const defaults = defaultOptions() as unknown as Record<string, unknown>;
    const removed = Object.fromEntries(previous.current.filter((k) => !(k in given)).map((k) => [k, defaults[k]]));
    previous.current = Object.keys(given);
    element.configure({ ...removed, ...given });
    // React 18 does not map className onto custom elements.
    if (className) element.setAttribute("class", className);
    else element.removeAttribute("class");
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

  return createElement(TAG, { ref: el, style });
});
