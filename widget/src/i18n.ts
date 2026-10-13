/**
 * Everything a visitor reads, in one table (`MESSAGES`, English), and the machinery
 * that swaps it for another language.
 *
 *   - `messages` option (code-only, also `heed.init({messages})`): overrides single
 *     strings, in any language; the strongest layer.
 *   - `lang` option / attribute: a language tag; empty picks the nearest `lang`
 *     attribute above the element, then `navigator.language`, then English.
 *   - The built-in translations (`locales.ts`: es, fr, de, pt) are not part of the
 *     widget bundle. They are fetched on first use, only for a visitor whose language
 *     is not English, through `setLocaleLoader` (capture.ts-style file next to the
 *     bundle when served; a dynamic `import()` in the npm package).
 *
 * The widget shows English until the translations arrive, then re-renders.
 */
/** Everything a visitor can be told or shown. `retryIn` is the one function (it takes seconds). */
export const MESSAGES = {
  label: "Feedback",
  heading: "Send feedback",
  categoryPrompt: "What is it about?",
  cat_bug: "Something is broken",
  cat_feature: "An idea or request",
  cat_question: "A question",
  cat_other: "Something else",
  titleLabel: "Title",
  bodyLabel: "Details",
  bodyPlaceholder: "What happened, and what did you expect?",
  honeypotLabel: "Leave this field empty",
  send: "Send",
  cancel: "Cancel",
  close: "Close",
  sending: "Sending…",
  received: "Thank you! Your report was sent.",
  held: "Thank you! Your report was received and will be reviewed before it is published.",
  view: "View your report",
  titleRequired: "Please give your report a short title.",
  noEndpoint: "This feedback form is not set up yet (no endpoint).",
  invalid: "The report could not be accepted. Please shorten or simplify it and try again.",
  too_large: "The report is too large to send. Please shorten it.",
  forbidden: "This site is not allowed to send reports to this feedback service.",
  bot: "We could not confirm you are human. Please reload the page and try again.",
  rate_limited: "Too many reports were sent recently. Please try again later.",
  unavailable: "The feedback service is unavailable right now. Please try again shortly.",
  network: "Could not reach the feedback service. Check your connection and try again.",
  timeout: "The feedback service took too long to answer. Please try again.",
  gateWaiting: "Please complete the check above, then wait a moment.",
  retryIn: (s: number) => `You can try again in ${s} seconds.`,
  note: "Sent with your report: this page's address (without its query) and your browser details.",
  noteShot: "Sent with your report: this page's address (without its query), your browser details, and the screenshot below if ticked. Form fields are blacked out in it.",
  shotLabel: "Include a screenshot of this page",
  shotAlt: "Screenshot to be sent",
  shotTaking: "Taking a screenshot…",
  shotFinishing: "Finishing the screenshot…",
  shotReview: "Check the screenshot below (untick it to leave it out), then press Send.",
  shotNoPreview: "The screenshot cannot be shown on this page, so it will not be sent.",
  shotDropped: "The screenshot was too large and was left out.",
  shotFailed: "No screenshot could be taken; the report will be sent without one.",
};

export type Messages = { [K in keyof typeof MESSAGES]: (typeof MESSAGES)[K] extends (...a: infer A) => string ? (...a: A) => string : string };
export type MessageOverrides = Partial<Messages>;

/** Languages that have built-in translations (English is the table above). */
export const LANGUAGES = ["en", "es", "fr", "de", "pt"] as const;

/**
 * The language to use: that of the first non-empty tag in `candidates` (like "fr-CA"),
 * "en" when it has no built-in translations. A page that says it is Japanese gets English
 * (or your `messages`), not whatever the browser's second choice is.
 */
export function pickLanguage(...candidates: (string | null | undefined)[]): string {
  const tag = candidates.find((t) => t?.trim());
  const base = (tag ?? "").trim().toLowerCase().split(/[-_]/)[0];
  return (LANGUAGES as readonly string[]).includes(base) ? base : "en";
}

/** Fetches the translations of one language; null when there are none. */
export type LocaleLoader = (lang: string) => Promise<MessageOverrides | null>;

const loaded: Record<string, MessageOverrides> = {};
const loading: Record<string, Promise<boolean>> = {};
let loader: LocaleLoader | null = null;

/** Set how translations are fetched (lib.ts and the served bundle each set theirs). */
export function setLocaleLoader(fn: LocaleLoader): void {
  loader = fn;
}

/**
 * Make `lang`'s translations available; resolves true once they are (at once for English
 * or an already loaded language), false when they cannot be fetched (English stays).
 */
export function ensureLocale(lang: string): Promise<boolean> {
  if (lang === "en" || loaded[lang]) return Promise.resolve(true);
  loading[lang] ??= (loader ? loader(lang) : Promise.resolve(null))
    .then((m) => {
      if (m) loaded[lang] = m;
      return !!m;
    })
    .catch((e) => (console.warn("heed: translations unavailable; showing English", e), false))
    .finally(() => delete loading[lang]);
  return loading[lang];
}

/** Whether `lang` needs no fetch (English, or already loaded). */
export const hasLocale = (lang: string): boolean => lang === "en" || lang in loaded;

/** English, then the language's translations (if loaded), then the embedder's own strings. */
export function messagesFor(lang: string, overrides: MessageOverrides | null | undefined): Messages {
  return { ...MESSAGES, ...loaded[lang], ...overrides } as Messages;
}

/** The label of a category (from `cat_<name>` messages; the name itself for an unknown one). */
export const categoryLabel = (m: Messages, category: string): string =>
  (m as unknown as Record<string, string>)[`cat_${category}`] ?? category;

/** Keep the known keys of `messages` with strings (a function for `retryIn`); warn about the rest. */
export function cleanMessages(value: unknown): MessageOverrides | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const out: Record<string, unknown> = {};
  for (const [key, v] of Object.entries(value)) {
    const expected = Object.prototype.hasOwnProperty.call(MESSAGES, key) ? typeof (MESSAGES as Record<string, unknown>)[key] : undefined;
    if (expected && typeof v === expected) out[key] = v;
    else console.warn(`heed: ignoring message ${key}`);
  }
  return out as MessageOverrides;
}

