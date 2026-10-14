/**
 * The widget's options: one table (`OPTION_SPECS`) says, for every option, its
 * default, the HTML attribute that sets it declaratively, and how to parse that
 * attribute's text. Everything else (the element's `observedAttributes`, the
 * attribute → option conversion, the merge with imperative options) is derived from
 * this table, so an option is added in exactly one place.
 *
 * Why not Zod here: the widget is a third-party embed loaded on other people's
 * sites, so every kilobyte counts, and the real validation happens on the server
 * (heed's Pydantic models). This table is the widget's schema.
 */
import { CONTRACT } from "./contract.gen.js";
import { type MessageOverrides, cleanMessages } from "./i18n.js";

export type Category = (typeof CONTRACT.categories)[number];
export type Position = "bottom-right" | "bottom-left" | "none";
export type PageUrlMode = "path" | "full";

/** Supplies a bot-gate token (Turnstile, hCaptcha…) at submit time; the seam for heed#5's gate. */
export type BotTokenProvider = () => string | null | undefined | Promise<string | null | undefined>;
/** Takes the screenshot; replaces the built-in rasterizer (tests, or apps that bundle heed). `options.signal` fires when nobody is waiting for it any more (the timeout, or the report was sent): stop then. */
export type ScreenshotTaker = (
  win: Window,
  options: { maskInputs: boolean; maskSelector: string; maxNodes: number; maxBytes: number; maxPixels: number; signal?: AbortSignal },
) => Promise<Blob | null>;
/** Extra environment facts to attach (app version, feature flags…), string → string. */
export type ExtraProvider = () => Record<string, string>;

export interface HeedOptions {
  /** Absolute or relative URL of heed's `POST /report` route. Required. */
  endpoint: string;
  /** Text of the floating launcher button; empty (the default) shows "Feedback" in the visitor's language. */
  label: string;
  /** Where the launcher sits; "none" hides it (open with `open()` instead). */
  position: Position;
  /** The dialog's heading; empty (the default) shows "Send feedback" in the visitor's language. */
  heading: string;
  /** Language of the widget's texts: a tag like "fr" or "pt-BR"; empty follows the page's `lang`, then the browser's. Built in: en, es, fr, de, pt. */
  lang: string;
  /** Overrides for single texts (any language), by key (see `MESSAGES`); code-only. A non-empty `label`/`heading` option takes precedence over the translation. */
  messages: MessageOverrides;
  /** Categories offered to the visitor, in order; the first is preselected. */
  categories: Category[];
  /** Categories that get the light intake: no screenshot, no browser details (only the page address and the embedder's `extra`). Default: feature and question; `""` for none. */
  lightCategories: Category[];
  /** "path" sends the page URL without query or fragment (privacy default); "full" sends all of it. */
  pageUrl: PageUrlMode;
  /** Send cookies with the report (only when the backend binds identity and allows credentials). */
  withCredentials: boolean;
  /** Give up on a submission after this many milliseconds. */
  timeoutMs: number;
  /** Form field the server reads the bot token from (HeedConfig.bot_token_field). */
  botTokenField: string;
  /** Hidden field humans leave empty (HeedConfig.honeypot_field); "" for none. */
  honeypotField: string;
  /** An opaque id the host app chooses to group one visitor's reports; none by default. */
  anonId: string;
  /** Offer a screenshot of the page (the visitor sees it and can untick it). */
  screenshot: boolean;
  /** Black out every field a visitor types into, selects, editable regions and iframes in the screenshot (password-like fields always are). */
  maskInputs: boolean;
  /** Elements to black out in the screenshot. */
  maskSelector: string;
  /** Give up on the screenshot after this many milliseconds (the report is then sent without it). */
  screenshotTimeoutMs: number;
  /** No screenshot of pages with more elements than this (cloning them would freeze the page). */
  screenshotMaxNodes: number;
  /** Built-in challenge to run before each send: "" (off), or a provider in GATES ("turnstile", "hcaptcha"). `botToken` overrides it. */
  botGate: string;
  /** The challenge provider's public site key (the secret stays on the server). */
  botSitekey: string;
  /** Overrides the provider's script URL (a self-hosted proxy, or a stand-in in tests); code-only. */
  botGateScript: string;
  /** Bot-gate token provider. Function options are set as properties or through `init`, never as attributes. */
  botToken: BotTokenProvider | null;
  /** Extra environment provider. */
  extra: ExtraProvider | null;
  /** Screenshot strategy; null uses the built-in, lazily loaded rasterizer. */
  takeScreenshot: ScreenshotTaker | null;
}

type Parse<T> = (text: string) => T | undefined;

interface OptionSpec<T> {
  default: T;
  /** Attribute name; absent for options only settable from code (functions). */
  attr?: string;
  parse?: Parse<T>;
  /** Validates a value given from code (instead of the type check against the default). */
  clean?: (value: unknown) => T | undefined;
}

type Specs = { [K in keyof HeedOptions]: OptionSpec<HeedOptions[K]> };

const text: Parse<string> = (s) => s;
const oneOf =
  <T extends string>(...allowed: T[]): Parse<T> =>
  (s) =>
    (allowed as string[]).includes(s) ? (s as T) : undefined;
export const parseBool: Parse<boolean> = (s) =>
  ["", "true", "1", "yes"].includes(s.toLowerCase()) ? true : ["false", "0", "no"].includes(s.toLowerCase()) ? false : undefined;
// setTimeout fires at once for delays past 2^31 - 1 ms, so longer ones are capped.
const MAX_TIMER_MS = 2_147_483_647;
const positiveInt: Parse<number> = (s) => {
  const n = Number(s);
  return Number.isInteger(n) && n > 0 ? Math.min(n, MAX_TIMER_MS) : undefined;
};
const warnedAnonIds = new Set<string>();
const anonId: Parse<string> = (s) => {
  if (s === "" || (s.length <= CONTRACT.limits.anon_id_chars && new RegExp(CONTRACT.patterns.anon_id).test(s))) return s;
  if (warnedAnonIds.has(s)) return undefined;
  warnedAnonIds.add(s);
  console.warn(`heed: ignoring anon-id ${JSON.stringify(s)}: the server accepts only ${CONTRACT.patterns.anon_id}`);
  return undefined;
};
const categories: Parse<Category[]> = (s) => {
  const known = CONTRACT.categories as readonly string[];
  const picked = s
    .split(/[\s,]+/)
    .filter((c) => known.includes(c)) as Category[];
  return picked.length ? [...new Set(picked)] : undefined;
};

const lightCategories: Parse<Category[]> = (s) => (s === "" ? [] : categories(s));

export const OPTION_SPECS: Specs = {
  endpoint: { default: "", attr: "endpoint", parse: text },
  label: { default: "", attr: "label", parse: text },
  position: { default: "bottom-right", attr: "position", parse: oneOf("bottom-right", "bottom-left", "none") },
  heading: { default: "", attr: "heading", parse: text },
  lang: { default: "", attr: "lang", parse: text },
  messages: { default: {}, clean: cleanMessages },
  categories: { default: [...CONTRACT.categories], attr: "categories", parse: categories },
  lightCategories: { default: ["feature", "question"], attr: "light-categories", parse: lightCategories },
  pageUrl: { default: "path", attr: "page-url", parse: oneOf("path", "full") },
  withCredentials: { default: false, attr: "with-credentials", parse: parseBool },
  timeoutMs: { default: 30_000, attr: "timeout-ms", parse: positiveInt },
  botTokenField: { default: CONTRACT.fields.bot_token, attr: "bot-token-field", parse: text },
  honeypotField: { default: CONTRACT.fields.honeypot ?? "", attr: "honeypot-field", parse: text },
  anonId: { default: "", attr: "anon-id", parse: anonId },
  screenshot: { default: true, attr: "screenshot", parse: parseBool },
  maskInputs: { default: true, attr: "mask-inputs", parse: parseBool },
  maskSelector: { default: "[data-heed-mask]", attr: "mask-selector", parse: text },
  screenshotTimeoutMs: { default: 10_000, attr: "screenshot-timeout-ms", parse: positiveInt },
  screenshotMaxNodes: { default: 5_000, attr: "screenshot-max-nodes", parse: positiveInt },
  botGate: { default: "", attr: "bot-gate", parse: (s) => s.toLowerCase() },
  botSitekey: { default: "", attr: "bot-sitekey", parse: text },
  botGateScript: { default: "" },
  botToken: { default: null },
  extra: { default: null },
  takeScreenshot: { default: null },
};

export const OPTION_ATTRIBUTES: string[] = Object.values(OPTION_SPECS as Record<string, OptionSpec<unknown>>)
  .map((s) => s.attr)
  .filter((a): a is string => !!a);

export function defaultOptions(): HeedOptions {
  const out: Record<string, unknown> = {};
  for (const [key, spec] of Object.entries(OPTION_SPECS as Record<string, OptionSpec<unknown>>)) {
    const d = spec.default;
    out[key] = Array.isArray(d) ? [...d] : d && typeof d === "object" ? { ...d } : d;
  }
  return out as unknown as HeedOptions;
}

/**
 * Options set by attributes (absent or unparsable ones are skipped). `source` is an
 * element (its attributes), or a lookup by attribute name, e.g. over a `<script>`
 * tag's `data-*` attributes.
 */
export function optionsFromAttributes(source: Element | ((attr: string) => string | null | undefined)): Partial<HeedOptions> {
  const get = typeof source === "function" ? source : (attr: string) => source.getAttribute(attr);
  const out: Record<string, unknown> = {};
  for (const [key, spec] of Object.entries(OPTION_SPECS as Record<string, OptionSpec<unknown>>)) {
    if (!spec.attr || !spec.parse) continue;
    const raw = get(spec.attr) ?? null;
    if (raw === null) continue;
    const value = spec.parse(raw.trim());
    if (value !== undefined) out[key] = value;
  }
  return out as Partial<HeedOptions>;
}

/** Keep only known option keys with valid values (drops typos and junk, with a console warning). */
export function cleanOptions(given: Record<string, unknown> | null | undefined): Partial<HeedOptions> {
  const out: Record<string, unknown> = {};
  if (!given) return out;
  for (const [key, spec] of Object.entries(OPTION_SPECS as Record<string, OptionSpec<unknown>>)) {
    if (!(key in given) || given[key] === undefined) continue;
    const value = cleanValue(given[key], spec);
    if (value === undefined) console.warn(`heed: ignoring option ${key}: unexpected value`, given[key]);
    else out[key] = value;
  }
  return out as Partial<HeedOptions>;
}

function cleanValue(value: unknown, spec: OptionSpec<unknown>): unknown {
  const dflt = spec.default;
  if (spec.clean) return spec.clean(value);
  if (dflt === null) return value === null || typeof value === "function" ? value : undefined;
  if (Array.isArray(dflt)) return Array.isArray(value) ? spec.parse?.(value.join(",")) : undefined;
  if (typeof value !== typeof dflt) return undefined;
  return spec.parse && typeof value !== "boolean" ? spec.parse(String(value)) : value;
}
