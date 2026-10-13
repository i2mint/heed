/**
 * Entry point of `heed-locales-<hash>.js`: registers the built-in translations for the
 * widget bundle that loaded this file (locale.ts, `servedLocaleLoader`).
 */
import { LOCALES } from "./locales.js";

(window as unknown as { __heedLocales?: typeof LOCALES }).__heedLocales = LOCALES;
