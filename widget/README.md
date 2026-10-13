# heed widget

The embeddable half of heed: a `<heed-feedback>` Web Component that lets any visitor of a site send a bug report or a request to a heed backend. It renders in an open Shadow DOM (its styles and the page's don't mix), uses a native `<dialog>` for the form, and posts a multipart report to heed's hardened `POST /report` route.

## Embed it

The heed backend serves the widget itself (`make_app` does by default; with your own app, `include_router(make_widget_router(), prefix="/heed")`). A site adds one tag:

```html
<script async src="https://feedback.example/heed/widget/heed.js"></script>
```

That is all: a "Feedback" button appears in the bottom-right corner, and reports go to the `report` route of the backend that served the script. The backend must list the embedding site in `allowed_origins` (see `misc/docs/embedding-security.md`).

`heed.js` is a loader of under 1 kB. It installs `window.heed`, then loads the widget bundle once the page has finished loading and the browser is idle (at once if the page asks to open the widget), so it never delays the page. Options go in `data-*` attributes, named like the element attributes below:

```html
<script async src="https://feedback.example/heed/widget/heed.js"
        data-label="Report a problem" data-position="bottom-left" data-categories="bug feature"></script>
```

`data-auto="false"` loads the widget without mounting it, for pages that call `heed.init()` themselves.

### From code

```js
heed.init({ label: "Help us improve", extra: () => ({ app_version: "2.3.1" }) }); // mount or reconfigure
heed.open();                                   // open the dialog (e.g. from your own menu item)
heed.close();
const off = heed.on("submitted", (detail) => console.log("report", detail.id));
heed.destroy();                                // remove the widget heed mounted
```

To call `heed` before the loader has arrived, define the queue first; calls are buffered and replayed in order:

```html
<script>window.heed = window.heed || function () { (heed.q = heed.q || []).push(arguments) };</script>
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

A pinned name stops existing when heed is upgraded, so re-pin then.

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
| `anonId` (`anon-id`) | none | An opaque id your app chooses to group one visitor's reports. The widget never invents or stores one. |
| `botToken` | none | `() => token` (or a promise of one): the bot-gate seam. Return a Turnstile/hCaptcha token when the backend's bot gate is on. |
| `extra` | none | `() => ({key: "value"})`: extra facts sent in `env.extra` (app version, flags…), trimmed to the server's limits. |

```js
const el = document.querySelector("heed-feedback");
el.botToken = () => turnstile.getResponse(widgetId);
el.extra = () => ({ app_version: "2.3.1" });
el.addEventListener("heed:submitted", (e) => console.log("report", e.detail.id));
```

Events (they bubble and cross the shadow boundary): `heed:open`, `heed:close`, `heed:submitted` (`{id, status, url}`; `status` is `held` when the backend's moderation queue holds it), `heed:error` (`{kind}`).

## What is sent

The title, details and category the visitor typed; the page URL (without query and fragment by default); and the browser environment (user agent, viewport, language, pixel ratio). Nothing is stored on the visitor's device. Screenshots are added in a later step of [i2mint/heed#6](https://github.com/i2mint/heed/issues/6).

## Theming

Set CSS custom properties on the element (`--heed-accent`, `--heed-accent-fg`, `--heed-bg`, `--heed-fg`, `--heed-muted`, `--heed-border`, `--heed-error`, `--heed-radius`, `--heed-font`, `--heed-z-index`, `--heed-offset`), or style the parts: `heed-feedback::part(launcher)`, `::part(dialog)`, `::part(heading)`, `::part(submit)`. Dark mode follows `prefers-color-scheme`.

## Content-Security-Policy

The widget needs no `'unsafe-inline'` and no `'unsafe-eval'`: its styles are constructed stylesheets and its DOM is built without `innerHTML`, so it also runs under `require-trusted-types-for 'script'`. Allow the backend's origin in `script-src` and `connect-src`. Under Trusted Types, the loader creates a policy named `heed` that only passes the bundle's own URL; if your CSP lists allowed policy names (`trusted-types ...`), add `heed`.

## Develop

```bash
cd widget
npm ci
npm run check    # TypeScript
npm test         # unit tests (vitest + jsdom)
npm run build    # writes heed/static/widget/ and its manifest; fails over the size budget
```

`src/contract.gen.ts` is generated from heed's Pydantic models (`python -m heed.widget contract > widget/src/contract.gen.ts`); a Python test fails when it is stale. The built files are committed so the Python package ships them; CI rebuilds and fails if they differ. `tests/test_widget_browser.py` runs the built widget in headless Chromium against a real backend (`pip install playwright uvicorn && playwright install chromium`).
