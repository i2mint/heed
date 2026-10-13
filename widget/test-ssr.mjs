// Server-side rendering smoke test of the built package (run after `npm run build:lib`):
// importing the entries and rendering <HeedFeedback> must work with no DOM.
import { createElement } from "react";
import { renderToString } from "react-dom/server";

await import("./dist/lib.js");
const { HeedFeedback } = await import("./dist/react.js");
const html = renderToString(createElement(HeedFeedback, { endpoint: "/heed/report", id: "fb" }));
if (!html.includes("<heed-feedback") || !html.includes('id="fb"')) throw new Error(`unexpected SSR output: ${html}`);
console.log("ssr ok:", html);
