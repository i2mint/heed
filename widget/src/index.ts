/**
 * Entry point of the widget bundle: registers `<heed-feedback>` and installs the
 * `window.heed` API (replaying calls the loader buffered).
 *
 * Sites normally load it through the loader (`heed.js`, see loader.ts). Loaded
 * directly, it still works: write a `<heed-feedback endpoint=...>` element, or call
 * `heed.init({ endpoint })`.
 */
import { installApi } from "./api";
import { defineHeedElement } from "./element";

export { HeedFeedbackElement, TAG, defineHeedElement } from "./element";
export { installApi, type HeedApi } from "./api";
export type { HeedOptions } from "./options";

defineHeedElement();
installApi();
