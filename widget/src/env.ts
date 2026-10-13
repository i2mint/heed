/**
 * The context gathered by default: the page address and the browser environment.
 *
 * Privacy by default: the page URL loses its query string and fragment unless the
 * embedder opts into `pageUrl: "full"` (queries carry tokens, emails, reset links),
 * and nothing here identifies the visitor beyond the user-agent string.
 */
import { CONTRACT } from "./contract.gen.js";
import type { ExtraProvider, PageUrlMode } from "./options.js";

const L = CONTRACT.limits;

/** Mirrors heed's `Environment` model (base.py). */
export interface EnvironmentPayload {
  user_agent?: string;
  browser?: string;
  os?: string;
  viewport?: string;
  locale?: string;
  device_pixel_ratio?: number;
  extra: Record<string, string>;
}

interface UADataLike {
  brands?: { brand: string; version: string }[];
  platform?: string;
}

const short = (s: string | undefined | null): string | undefined => (s ? s.slice(0, L.short_chars) : undefined);

export function collectEnvironment(win: Window, extra?: ExtraProvider | null): EnvironmentPayload {
  const nav = win.navigator;
  const uaData = (nav as Navigator & { userAgentData?: UADataLike }).userAgentData;
  const brand = uaData?.brands?.find((b) => !/not.?a.?brand/i.test(b.brand));
  const dpr = win.devicePixelRatio;
  return {
    user_agent: short(nav.userAgent),
    browser: short(brand ? `${brand.brand} ${brand.version}` : undefined),
    os: short(uaData?.platform),
    viewport: `${win.innerWidth}x${win.innerHeight}`,
    locale: short(nav.language),
    device_pixel_ratio: dpr > 0 && dpr <= 100 ? dpr : undefined,
    extra: collectExtra(extra),
  };
}

/** The embedder's extra facts, trimmed to what the server accepts; a throwing provider adds nothing. */
export function collectExtra(extra?: ExtraProvider | null): Record<string, string> {
  if (!extra) return {};
  let given: unknown;
  try {
    given = extra();
  } catch (e) {
    console.warn("heed: the extra provider threw; sending no extra facts", e);
    return {};
  }
  if (!given || typeof given !== "object") return {};
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(given as Record<string, unknown>)) {
    if (Object.keys(out).length >= L.extra_items) break;
    const key = k.trim().slice(0, L.extra_key_chars);
    if (key && v !== undefined && v !== null) out[key] = String(v).slice(0, L.short_chars);
  }
  return out;
}

/** The page address to report: "" for non-http(s) pages, never longer than the server accepts. */
export function reportPageUrl(href: string, mode: PageUrlMode): string {
  let url: URL;
  try {
    url = new URL(href);
  } catch {
    return "";
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return "";
  // Credentials in a URL are never worth reporting.
  url.username = "";
  url.password = "";
  const pathOnly = url.origin + url.pathname;
  const chosen = mode === "full" ? url.href : pathOnly;
  if (chosen.length <= L.url_chars) return chosen;
  return pathOnly.length <= L.url_chars ? pathOnly : url.origin + "/";
}
