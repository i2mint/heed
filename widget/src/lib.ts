/**
 * Entry point for apps that bundle heed themselves (`import "heed-widget"`), rather
 * than loading the served script.
 *
 * Importing it registers `<heed-feedback>`; nothing is mounted until you write the
 * element (or use the React wrapper, `heed-widget/react`). The screenshot rasterizer
 * is imported lazily from here, so the app's bundler splits it into a chunk that is
 * only fetched when a visitor opens the widget.
 */
import { screenshotsAllowed, setScreenshotStrategy } from "./capture.js";
import { defineHeedElement } from "./element.js";
import type { ScreenshotTaker } from "./options.js";

/** Screenshots through a lazily imported rasterizer chunk. */
export const moduleScreenshot: ScreenshotTaker = async (win, options) => {
  if (!screenshotsAllowed(win.document)) return null;
  const { capture } = await import("./raster.js");
  return capture(win, options);
};

setScreenshotStrategy(moduleScreenshot);
defineHeedElement();

export { HeedFeedbackElement, TAG, defineHeedElement } from "./element.js";
export { MESSAGES } from "./submit.js";
export type { BotTokenProvider, Category, ExtraProvider, HeedOptions, Position, ScreenshotTaker } from "./options.js";
