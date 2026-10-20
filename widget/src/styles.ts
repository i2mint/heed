/**
 * The widget's stylesheet, applied inside its shadow root.
 *
 * It is adopted as a constructed stylesheet (`adoptedStyleSheets`), which a page's
 * Content-Security-Policy does not treat as inline style, so embedding needs no
 * `'unsafe-inline'` in `style-src` (misc/docs/embedding-security.md). Browsers
 * without constructed stylesheets get a `<style>` element instead.
 *
 * Theming: set these custom properties on the `heed-feedback` element (they inherit
 * through the shadow boundary) or style the exposed parts with `::part(...)`:
 * `--heed-accent`, `--heed-accent-fg`, `--heed-bg`, `--heed-fg`, `--heed-muted`,
 * `--heed-border`, `--heed-radius`, `--heed-font`, `--heed-z-index`, `--heed-offset`.
 */
export const CSS = `
:host { all: initial; }
:host([hidden]) { display: none; }
* { box-sizing: border-box; }
.root {
  --_accent: var(--heed-accent, #2563eb);
  --_accent-fg: var(--heed-accent-fg, #fff);
  --_bg: var(--heed-bg, #fff);
  --_fg: var(--heed-fg, #111827);
  --_muted: var(--heed-muted, #4b5563);
  --_border: var(--heed-border, #d1d5db);
  --_error: var(--heed-error, #b91c1c);
  --_radius: var(--heed-radius, 10px);
  --_offset: var(--heed-offset, 20px);
  font: 14px/1.45 var(--heed-font, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif);
  color: var(--_fg);
}
@media (prefers-color-scheme: dark) {
  .root {
    --_bg: var(--heed-bg, #1f2937);
    --_fg: var(--heed-fg, #f9fafb);
    --_muted: var(--heed-muted, #d1d5db);
    --_border: var(--heed-border, #4b5563);
    --_error: var(--heed-error, #fca5a5);
  }
}
.launcher {
  position: fixed; bottom: var(--_offset); z-index: var(--heed-z-index, 2147483000);
  font: inherit; font-weight: 600; cursor: pointer;
  padding: 10px 16px; border: 0; border-radius: 999px;
  background: var(--_accent); color: var(--_accent-fg);
  box-shadow: 0 4px 14px rgb(0 0 0 / 0.25);
}
.launcher.bottom-right { right: var(--_offset); }
.launcher.bottom-left { left: var(--_offset); }
.launcher:focus-visible, button:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible {
  outline: 2px solid var(--_accent); outline-offset: 2px;
}
dialog {
  width: min(440px, calc(100vw - 32px)); max-height: calc(100vh - 32px);
  padding: 20px; border: 1px solid var(--_border); border-radius: var(--_radius);
  background: var(--_bg); color: var(--_fg); font: inherit;
  box-shadow: 0 20px 50px rgb(0 0 0 / 0.3);
}
dialog::backdrop { background: rgb(0 0 0 / 0.35); }
h2 { margin: 0 0 12px; font-size: 18px; line-height: 1.3; }
form, .done { display: grid; gap: 12px; }
[hidden] { display: none !important; }
label { display: grid; gap: 4px; font-weight: 600; }
input, select, textarea {
  font: inherit; font-weight: 400; color: inherit; width: 100%;
  padding: 8px 10px; border: 1px solid var(--_border); border-radius: 6px; background: transparent;
}
select option { color: #111827; background: #fff; }
textarea { min-height: 110px; resize: vertical; }
.note { margin: 0; color: var(--_muted); font-size: 12.5px; }
.messages { display: grid; gap: 4px; }
.messages:has(.status:empty + .status:empty) { display: none; }
.status { margin: 0; }
.status.error { color: var(--_error); }
.actions { display: flex; justify-content: flex-end; gap: 8px; }
.actions button {
  font: inherit; font-weight: 600; cursor: pointer; padding: 8px 14px;
  border-radius: 6px; border: 1px solid var(--_border); background: transparent; color: inherit;
}
.actions button.primary { background: var(--_accent); color: var(--_accent-fg); border-color: transparent; }
.actions button:disabled { opacity: 0.6; cursor: progress; }
.done a { color: var(--_accent); }
.shot, .logs { display: grid; gap: 6px; }
label.check { display: flex; align-items: center; gap: 8px; font-weight: 400; }
label.check input { width: auto; margin: 0; }
.preview { max-width: 100%; max-height: 240px; object-fit: contain; justify-self: start; border: 1px solid var(--_border); border-radius: 6px; }
.hp { position: absolute; left: -10000px; top: auto; width: 1px; height: 1px; overflow: hidden; }
.gate:empty { display: none; }
`;

let shared: CSSStyleSheet | null = null;

/** Attach the stylesheet to `root`, constructed when the browser supports it. */
export function applyStyles(root: ShadowRoot): void {
  const canAdopt = "adoptedStyleSheets" in root && typeof CSSStyleSheet === "function" && "replaceSync" in CSSStyleSheet.prototype;
  if (canAdopt) {
    try {
      shared ??= new CSSStyleSheet();
      if (!shared.cssRules.length) shared.replaceSync(CSS);
      root.adoptedStyleSheets = [...root.adoptedStyleSheets, shared];
      return;
    } catch {
      // fall through to a <style> element
    }
  }
  const style = root.ownerDocument.createElement("style");
  style.textContent = CSS;
  root.append(style);
}
