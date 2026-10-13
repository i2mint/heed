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

## Options

Every option has a default; set it as an element attribute, as a `data-*` attribute on the loader tag, or from code with `heed.init({...})` / `el.configure({...})`. Function-valued options are code-only. `endpoint` defaults to the backend that served the loader.

| Option (attribute) | Default | What it does |
|---|---|---|
| `endpoint` (`endpoint`) | the loader's backend | URL of the backend's `POST /report`. Required when the bundle is used without the loader. |
| `label` (`label`) | `Feedback` | Launcher button text. |
| `position` (`position`) | `bottom-right` | `bottom-left`, or `none` for no launcher (open it with `el.open()`). |
| `heading` (`heading`) | `Send feedback` | Dialog heading. |
| `categories` (`categories`) | `bug feature question other` | Categories offered, first preselected; one category hides the picker. |
| `pageUrl` (`page-url`) | `path` | `path` reports the page URL without its query and fragment (they often carry tokens); `full` sends it all. |
| `withCredentials` (`with-credentials`) | `false` | Send cookies, for a backend that binds identity (needs `cors_allow_credentials` server-side). |
| `timeoutMs` (`timeout-ms`) | `30000` | Give up on a submission after this long. |
| `botTokenField` (`bot-token-field`) | `bot_token` | Must match the server's `HEED_BOT_TOKEN_FIELD`. |
| `honeypotField` (`honeypot-field`) | `hp_website` | Must match the server's `HEED_HONEYPOT_FIELD`; empty for none. |
| `screenshot` (`screenshot`) | `true` | Offer a screenshot of the visible page (the visitor sees a preview and can untick it). |
| `maskInputs` (`mask-inputs`) | `true` | In the screenshot, text fields show bullets; selects, file inputs and editable regions are blacked out. Password fields always show bullets. |
| `maskSelector` (`mask-selector`) | `[data-heed-mask]` | Elements blacked out in the screenshot. |
| `screenshotTimeoutMs` (`screenshot-timeout-ms`) | `10000` | Give up on the screenshot after this long; the report is sent without it. |
| `anonId` (`anon-id`) | none | An opaque id your app chooses to group one visitor's reports. The widget never invents or stores one. |
| `botToken` | none | `() => token` (or a promise of one): the bot-gate seam. Return a Turnstile/hCaptcha token when the backend's bot gate is on. |
| `extra` | none | `() => ({key: "value"})`: extra facts sent in `env.extra` (app version, flags…), trimmed to the server's limits. |
| `takeScreenshot` | built-in | `(window, {maskInputs, maskSelector, maxBytes, maxPixels}) => Promise<Blob \| null>`: replace the screenshot strategy (an app that bundles heed itself, or its own capture). |

```js
const el = document.querySelector("heed-feedback");
el.botToken = () => turnstile.getResponse(widgetId);
el.extra = () => ({ app_version: "2.3.1" });
el.addEventListener("heed:submitted", (e) => console.log("report", e.detail.id));
```

Events (they bubble and cross the shadow boundary): `heed:open`, `heed:close`, `heed:submitted` (`{id, status, url}`; `status` is `held` when the backend's moderation queue holds it), `heed:error` (`{kind}`).

## What is sent

The title, details and category the visitor typed; the page URL (without query and fragment by default); the browser environment (user agent, viewport, language, pixel ratio); and, unless the visitor unticks it, a screenshot of the visible page. Nothing is stored on the visitor's device.

## Screenshots

When the dialog opens, the widget loads its rasterizer (`heed-raster-<hash>.js`, about 10 kB gzipped, fetched only then and pinned by SRI from the bundle) and re-renders the part of the page the visitor sees, leaving the widget itself out. The visitor sees a preview and can untick it. The capture runs in the background; Send waits for it (up to `screenshotTimeoutMs`), and any failure just means the report goes without one.

Privacy is applied to the cloned page, before anything is drawn, so masked content never reaches the image: password fields always show bullets; with `maskInputs` (the default) all text fields show bullets and selects, file inputs and editable regions are blacked out. Mark anything else sensitive with `data-heed-mask` (blacked out) or `data-heed-ignore` (left out entirely, e.g. a chat widget):

```html
<div class="account-balance" data-heed-mask>…</div>
```

Limits to know: it is a DOM re-rendering, not true pixels, so cross-origin iframes, videos, tainted canvases and some CSS come out blank or approximate. It needs a page whose Content-Security-Policy allows inline styles and does not enforce Trusted Types; on a stricter page the widget detects it once, logs a console warning, and sends reports without a screenshot (set `screenshot="false"` / `data-screenshot="false"` to skip even the check). The image is at most 8 megapixels, PNG, falling back to JPEG to stay under the server's size cap.

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
```

Bump `version` in `package.json` for every widget change that ships: hashed files are retained per version (the previous five), and a rebuild without a bump replaces the current version's files.

`src/contract.gen.ts` is generated from heed's Pydantic models (`python -m heed.widget contract > widget/src/contract.gen.ts`); a Python test fails when it is stale. The built files are committed so the Python package ships them; CI rebuilds and fails if they differ. `tests/test_widget_browser.py` runs the built widget in headless Chromium against a real backend (`pip install playwright uvicorn && playwright install chromium`).
