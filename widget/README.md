# heed widget

The embeddable half of heed: a `<heed-feedback>` Web Component that lets any visitor of a site send a bug report or a request to a heed backend. It renders in an open Shadow DOM (its styles and the page's don't mix), uses a native `<dialog>` for the form, and posts a multipart report to heed's hardened `POST /report` route.

## Embed it

The heed backend serves the widget itself (`make_app` does by default; with your own app, `include_router(make_widget_router(), prefix="/heed")`). A site adds one tag:

```html
<script async src="https://feedback.example/heed/widget/heed.js"></script>
```

That is all: a "Feedback" button appears in the bottom-right corner, and reports go to the `report` route of the backend that served the script. The backend must list the embedding site in `allowed_origins` (see `misc/docs/embedding-security.md`).

`heed.js` is a loader of about 1 kB. It installs `window.heed`, then loads the widget bundle once the page has finished loading and the browser is idle (at once if the page asks to open the widget), so it never delays the page. Options go in `data-*` attributes, named like the element attributes below:

```html
<script async src="https://feedback.example/heed/widget/heed.js"
        data-label="Report a problem" data-position="bottom-left" data-categories="bug feature"></script>
```

`data-auto="false"` loads the widget without mounting it, for pages that call `heed.init()` themselves. A page that writes its own `<heed-feedback>` element keeps it: the loader then mounts nothing (and lends the element its endpoint if it has none). The mounted widget is put back when a page swaps `<body>` on navigation (Turbo, htmx boost, pjax).

### From code

```js
heed.init({ label: "Help us improve", extra: () => ({ app_version: "2.3.1" }) }); // mount or reconfigure
heed.open();                                   // open the dialog (e.g. from your own menu item)
heed.close();
const off = heed.on("submitted", (detail) => console.log("report", detail.id)); // off() stops listening
heed.ready((api) => console.log("heed", api.version)); // once the bundle has loaded
heed.destroy();                                // remove the widget heed mounted
```

Until the bundle has loaded, calls are buffered and return nothing, except `on`, whose unsubscribe works at any time. `heed.init()` returns the `<heed-feedback>` element once `heed.loaded` is true; use `heed.ready()` to wait for that.

To call `heed` before the loader has arrived, define the queue first; calls are buffered and replayed in order:

```html
<script>window.heed = typeof window.heed == "function" ? window.heed : function () { (heed.q = heed.q || []).push(arguments) };</script>
<script async src="https://feedback.example/heed/widget/heed.js"></script>
<script>heed("init", { heading: "Tell us what happened" });</script>
```

(Under a CSP without `'unsafe-inline'`, give those inline scripts a nonce, or call `heed` from your own bundled code.)

### Pinning a version

`heed.js` and `heed-widget.js` are stable names that always serve the current build (cached for five minutes). Every build is also published under content-hashed names that are cached forever; their `integrity` values are in `heed/static/widget/manifest.json` (`entries.loader`, `entries.widget`). Pinning the hashed loader with Subresource Integrity pins the whole chain, because the loader carries the bundle's hashed name and SRI hash:

```html
<script async src="https://feedback.example/heed/widget/heed-<hash>.js"
        integrity="sha384-…" crossorigin="anonymous"></script>
```

Hashed files of the previous five widget versions stay published after an upgrade, so a pin, a loader cached before the upgrade, or a replica still on the old version keeps working; re-pin within those five versions.

### From a CDN or another path

The loader's default endpoint is the `report` route beside the `widget/` folder it was served from. Served from anywhere else (a CDN, a renamed path), give it `data-endpoint`, and mirror the hashed files next to it, since the loader fetches the bundle relative to its own URL. If the loader is injected in a way that leaves `document.currentScript` empty and renamed, mark its tag with `data-heed-loader`. Under a nonce-based CSP the loader passes its own nonce to the bundle it adds.

`window.heed` is the widget's global name. If the page already uses it for something else, the loader leaves it alone, warns in the console, and does not load.

### Without the loader

The bundle works on its own too: load `heed-widget.js` and write the element, which is handy for frameworks that render markup:

```html
<script src="https://feedback.example/heed/widget/heed-widget.js" defer></script>
<heed-feedback endpoint="https://feedback.example/heed/report"></heed-feedback>
```

### React

A thin wrapper over the same element, for apps that bundle heed (`heed-widget/react`; the package is not on npm yet, see [i2mint/heed#40](https://github.com/i2mint/heed/issues/40)). Props are the options below plus event callbacks; a ref gives `open()` and `close()`:

```tsx
import { useRef } from "react";
import { HeedFeedback, type HeedFeedbackHandle } from "heed-widget/react";

export function Feedback() {
  const feedback = useRef<HeedFeedbackHandle>(null);
  return (
    <>
      <button onClick={() => feedback.current?.open()}>Report a problem</button>
      <HeedFeedback
        ref={feedback}
        endpoint="https://feedback.example/heed/report"
        position="none"
        extra={() => ({ app_version: "2.3.1" })}
        onSubmitted={(report) => console.log("sent", report.id)}
        style={{ "--heed-accent": "#0a7" }}
      />
    </>
  );
}
```

`id`, `title`, `data-*` and `aria-*` props go onto the element. The package is ES modules only, and safe to import during server-side rendering (Next.js, Remix): the element is defined only in the browser, and the entries are marked `"use client"`. If the page also loads the served script (or another copy of heed), whichever defines `<heed-feedback>` first is the one in use; heed warns in the console. Function props (`botToken`, `extra`, `takeScreenshot`) work in React 18 and 19 alike (the wrapper passes every prop through `configure()`), and a removed prop goes back to its default. Bundled this way, the screenshot rasterizer is a separate chunk your bundler loads only when the dialog opens. Without a bundler, the loader's `<heed-feedback>` works in JSX as is.

## Options

Every option has a default; set it as an element attribute, as a `data-*` attribute on the loader tag, or from code with `heed.init({...})` / `el.configure({...})`. Function-valued options are code-only. `endpoint` defaults to the backend that served the loader.

| Option (attribute) | Default | What it does |
|---|---|---|
| `endpoint` (`endpoint`) | the loader's backend | URL of the backend's `POST /report`. Required when the bundle is used without the loader. |
| `label` (`label`) | `Feedback` | Launcher button text. |
| `position` (`position`) | `bottom-right` | `bottom-left`, or `none` for no launcher (open it with `el.open()`). |
| `heading` (`heading`) | `Send feedback` | Dialog heading. |
| `lang` (`lang`) | follows the page | Language of the widget's texts: `en`, `es`, `fr`, `de` or `pt` (a tag like `fr-CA` works). Empty uses the nearest `lang` attribute above the widget (`<html lang>`), then the browser's language, then English. See "Languages". |
| `messages` | none | `{send: "Go"}`: override single texts, in any language (code-only: `el.configure({messages})`, `heed.init({messages})`, a prop in React). Keys are those of `MESSAGES` in `src/i18n.ts`. |
| `categories` (`categories`) | `bug feature question other` | Categories offered, first preselected; one category hides the picker. |
| `pageUrl` (`page-url`) | `path` | `path` reports the page URL without its query and fragment (they often carry tokens); `full` sends it all. |
| `withCredentials` (`with-credentials`) | `false` | Send cookies, for a backend that binds identity (needs `cors_allow_credentials` server-side). |
| `timeoutMs` (`timeout-ms`) | `30000` | Give up on a submission after this long. |
| `botTokenField` (`bot-token-field`) | `bot_token` | Must match the server's `HEED_BOT_TOKEN_FIELD`. |
| `honeypotField` (`honeypot-field`) | `hp_website` | Must match the server's `HEED_HONEYPOT_FIELD`; empty for none. |
| `screenshot` (`screenshot`) | `true` | Offer a screenshot of the visible page (the visitor sees a preview and can untick it). |
| `maskInputs` (`mask-inputs`) | `true` | In the screenshot, black out every field a visitor types into, selects, editable regions and iframes. Password-like fields always are. |
| `maskSelector` (`mask-selector`) | `[data-heed-mask]` | Elements blacked out in the screenshot. |
| `screenshotTimeoutMs` (`screenshot-timeout-ms`) | `10000` | Give up on the screenshot after this long; the report is sent without it. |
| `screenshotMaxNodes` (`screenshot-max-nodes`) | `5000` | No screenshot of pages with more elements than this (cloning them would freeze the tab). |
| `anonId` (`anon-id`) | none | An opaque id your app chooses to group one visitor's reports. The widget never invents or stores one. |
| `botGate` (`bot-gate`) | none (off) | Built-in challenge: `turnstile` or `hcaptcha`. Loads the provider's script when the dialog opens, runs it for every send. Needs `botSitekey`; see "Bot gate" below. |
| `botSitekey` (`bot-sitekey`) | none | The challenge provider's public site key. The secret key stays on the server (`bot_secret`, `HEED_BOT_SECRET`). |
| `botGateScript` | provider's | Code-only: another URL for the provider's script (a self-hosted proxy, a test stand-in). |
| `botToken` | none | `() => token` (or a promise of one): the bot-gate seam. Return a Turnstile/hCaptcha token when the backend's bot gate is on. |
| `extra` | none | `() => ({key: "value"})`: extra facts sent in `env.extra` (app version, flags…), trimmed to the server's limits. |
| `takeScreenshot` | built-in | `(window, {maskInputs, maskSelector, maxNodes, maxBytes, maxPixels}) => Promise<Blob \| null>`: replace the screenshot strategy (an app that bundles heed itself, or its own capture). |

```js
const el = document.querySelector("heed-feedback");
el.botToken = () => turnstile.getResponse(widgetId);
el.extra = () => ({ app_version: "2.3.1" });
el.addEventListener("heed:submitted", (e) => console.log("report", e.detail.id));
```

Events (they bubble and cross the shadow boundary): `heed:open`, `heed:close`, `heed:submitted` (`{id, status, url}`; `status` is `held` when the backend's moderation queue holds it), `heed:error` (`{kind}`).

## Languages

All visitor-facing text lives in one table (`MESSAGES`, `src/i18n.ts`). Built in: English, Spanish, French, German and Portuguese. The widget picks the language from its `lang` option, else the page's `lang` attribute, else `navigator.language`; anything else (a Japanese page, say) gets English, or your own texts:

```js
heed.init({ lang: "ja", messages: { label: "フィードバック", heading: "フィードバックを送る", send: "送信", cancel: "キャンセル", close: "閉じる" /* …every key you want to change */ } });
```

`messages` is the strongest layer (a later `messages` replaces the earlier one; `{}` clears it): English, then the language's translations, then your overrides. `retryIn` is the one function (`(seconds) => string`); the category names are the `cat_<name>` keys. A `label` or `heading` option you set yourself also beats the translation.

The translations are not in the widget bundle. A visitor whose language is not English triggers one extra request, for `heed-locales-<hash>.js` next to the bundle (content-hashed and SRI-pinned like the rasterizer, nothing new for the CSP or Trusted Types); the widget shows English until it arrives. If it cannot be fetched, the widget stays in English and warns in the console. With the npm package your bundler splits the translations into a chunk that only those visitors download. To follow your own language switcher, call `el.configure({ lang })`.

## Bot gate

When the backend demands a challenge token (`bot_gate`, [embedding-security.md](../misc/docs/embedding-security.md)), the widget can run Cloudflare Turnstile (or hCaptcha, `data-bot-gate="hcaptcha"`) itself:

```html
<script async src="https://feedback.example/heed/widget/heed.js"
        data-bot-gate="turnstile" data-bot-sitekey="0x4AAAA…"></script>
```

Off unless `bot-gate` is set; nothing is loaded from the provider until the visitor opens the dialog. The challenge is rendered inside the dialog (a modal dialog makes the rest of the page inert, so an interactive challenge placed outside it could not be clicked) in Turnstile's "managed, interaction-only" mode: most visitors see nothing, and a token is requested for each send, so it is never stale. The challenge's box is shown only while it needs the visitor. The time the challenge takes counts against `timeoutMs`; a challenge the visitor never completes shows the same "could not confirm you are human" message as a failed one, and closing the dialog mid-challenge cancels that send (the draft stays). If the server refuses the screenshot as too large, the report is resent without it, which runs a second challenge (the server checks the token before the screenshot's size, so the first is spent), with the status line saying why; if the dialog was closed meanwhile, nothing is resent. A failed or blocked challenge shows the "could not confirm you are human" message and sends nothing. An explicit `botToken` provider always takes precedence. The page's CSP must allow the provider (table in embedding-security.md); under Trusted Types add the policy `heed-bot-gate` (and `'allow-duplicates'` if the page loads two heed bundles); it passes the provider's script URL, or the `botGateScript` your own code sets. hCaptcha runs in its invisible mode, and the widget asks it to place any challenge it chooses to show inside the dialog (its `challenge-container` parameter); hCaptcha's test key never asks for one, so that placement is untested against a real challenge. For another provider, keep using `botToken`; `GATES` in `src/botgate.ts` is where an adapter would be added.

## What is sent

The title, details and category the visitor typed; the page URL (without query and fragment by default); the browser environment (user agent, viewport, language, pixel ratio); and, unless the visitor unticks it, a screenshot of the visible page. Nothing is stored on the visitor's device.

## Screenshots

When the dialog opens, the widget loads its rasterizer (`heed-raster-<hash>.js`, about 11 kB gzipped, fetched only then and pinned by SRI from the bundle) and re-renders the part of the page the visitor sees, leaving the widget itself out. The capture runs in the background and the visitor sees a preview with an "Include a screenshot" checkbox. **Only a screenshot the visitor has been shown is ever sent**: pressing Send while it is still being taken finishes it, shows it, and asks them to check it and press Send again; if the preview cannot be displayed (a CSP without `img-src blob:`), it is not sent. Any failure means the report goes without one.

Privacy is enforced twice before the image leaves the page. First, the cloned page is scrubbed before it is drawn: field values and the contents of masked elements are removed, decided on the live page (so selectors, input types and editability mean what they mean to the browser). Second, black boxes are painted over the final image wherever the live page has masked content, including children positioned or overflowing outside their element. What is masked:

- password-like fields, always (`type=password`, or `autocomplete` `current-password`, `new-password`, `one-time-code`, `cc-*`);
- with `maskInputs` (the default): every field a visitor types into, selects, editable regions, and every iframe or embed;
- whatever `maskSelector` matches (`[data-heed-mask]` by default):

```html
<div class="account-balance" data-heed-mask>…</div>
```

Mark an element `data-heed-ignore` to leave it out entirely (a chat widget, say).

Limits to know: it is a DOM re-rendering, not true pixels, so videos, cross-origin iframes, tainted canvases and some CSS come out blank or approximate. Cloning a page costs main-thread time in proportion to its size, so pages with more than `screenshotMaxNodes` elements (5000) get no screenshot rather than a frozen tab. It needs a page whose Content-Security-Policy allows inline styles and `img-src data: blob:` and does not enforce Trusted Types; on a stricter page the widget detects it once before downloading anything (the inline-style probe may itself produce one CSP violation report), logs a console warning, and sends reports without a screenshot (`screenshot="false"` skips even the check). The image is at most 8 megapixels, PNG, falling back to JPEG, and kept under 60% of the server's size cap; if the server still refuses it as too large, the report is re-sent without it.

## Theming

Set CSS custom properties on the element (`--heed-accent`, `--heed-accent-fg`, `--heed-bg`, `--heed-fg`, `--heed-muted`, `--heed-border`, `--heed-error`, `--heed-radius`, `--heed-font`, `--heed-z-index`, `--heed-offset`), or style the parts: `heed-feedback::part(launcher)`, `::part(dialog)`, `::part(heading)`, `::part(submit)`. Dark mode follows `prefers-color-scheme`.

## Content-Security-Policy

The widget itself needs no `'unsafe-inline'` and no `'unsafe-eval'`: its styles are constructed stylesheets and its DOM is built without `innerHTML`, so it also runs under `require-trusted-types-for 'script'`. Allow the backend's origin in `script-src` and `connect-src`. Screenshots are the exception: they need `style-src 'unsafe-inline'`, `img-src data: blob:`, and no Trusted Types enforcement (see above). Under Trusted Types, the loader creates a policy named `heed` that only passes the bundle's own URL; if your CSP lists allowed policy names (`trusted-types ...`), add `heed` (and `'allow-duplicates'` if the page creates a policy of that name itself); otherwise the loader warns in the console and the widget does not load.

## Develop

```bash
cd widget
npm ci
npm run check    # TypeScript
npm test         # unit tests (vitest + jsdom)
npm run build    # writes heed/static/widget/ and its manifest; fails over the size budget
npm run build:lib  # the ES module package in dist/ (heed-widget, heed-widget/react), with types
```

Bump `version` in `package.json` for every widget change that ships: hashed files are retained per version (the previous five), and a rebuild without a bump replaces the current version's files.

`src/contract.gen.ts` is generated from heed's Pydantic models (`python -m heed.widget contract > widget/src/contract.gen.ts`); a Python test fails when it is stale. The built files are committed so the Python package ships them; CI rebuilds and fails if they differ. `tests/test_widget_browser.py` runs the built widget in headless Chromium against a real backend (`pip install playwright uvicorn && playwright install chromium`).
