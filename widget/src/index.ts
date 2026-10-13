/**
 * Entry point of the widget bundle: registers `<heed-feedback>`.
 *
 * Loading this file is enough for the declarative form:
 *   <script src="https://feedback.example/heed/widget/heed-widget-<hash>.js"></script>
 *   <heed-feedback endpoint="https://feedback.example/heed/report"></heed-feedback>
 */
import { defineHeedElement } from "./element";

export { HeedFeedbackElement, TAG, defineHeedElement } from "./element";
export type { HeedOptions } from "./options";

defineHeedElement();
