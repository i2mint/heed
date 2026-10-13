/**
 * How the built-in translations (locales.ts) reach the widget, per way of shipping it:
 * the served bundle fetches `heed-locales-<hash>.js` next to itself on first use
 * (sibling.ts); the npm package imports them lazily so the app's bundler splits them
 * into a chunk fetched only for visitors who need it.
 */
import type { LocaleLoader } from "./i18n.js";
import { loadSibling } from "./sibling.js";
import type { LOCALES } from "./locales.js";

declare const __HEED_LOCALES__: string;
declare const __HEED_LOCALES_INTEGRITY__: string;

type Table = typeof LOCALES;

let table: Promise<Table> | null = null;

export const servedLocaleLoader: LocaleLoader = async (lang) => {
  const w = window as unknown as { __heedLocales?: Table };
  // One download however many languages are asked for; a failure may be retried.
  table ??= loadSibling(window, __HEED_LOCALES__, __HEED_LOCALES_INTEGRITY__, () => w.__heedLocales, "translations");
  try {
    return (await table)[lang] ?? null;
  } catch (e) {
    table = null;
    throw e;
  }
};

export const moduleLocaleLoader: LocaleLoader = async (lang) => (await import("./locales.js")).LOCALES[lang] ?? null;
