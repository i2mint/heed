/**
 * `<heed-feedback>`: the feedback widget as a Web Component.
 *
 * An *open* shadow root isolates its markup and styles from the host page (isolation,
 * not security: the page can still reach in). The form lives in a native `<dialog>`
 * opened with `showModal()`, which gives focus trapping, Esc to close and focus
 * restoration without a focus-trap library (those break inside Shadow DOM).
 *
 * Declarative use: attributes (see `OPTION_SPECS` in options.ts).
 *   <heed-feedback endpoint="https://feedback.example/heed/report"></heed-feedback>
 * Imperative use: `el.configure({...})`, `el.open()`, `el.close()`, and the
 * `botToken` / `extra` properties for options that are functions.
 *
 * Events (bubbling, composed): `heed:open`, `heed:close`,
 * `heed:submitted` (detail: `{id, status, url}`), `heed:error` (detail: `{kind}`).
 *
 * The DOM is built with `createElement`, never `innerHTML`, so the widget also runs
 * under a Trusted Types policy.
 */
import { collectEnvironment, reportPageUrl } from "./env.js";
import {
  type BotTokenProvider,
  type Category,
  type ExtraProvider,
  type HeedOptions,
  OPTION_ATTRIBUTES,
  cleanOptions,
  defaultOptions,
  optionsFromAttributes,
} from "./options.js";
import { CONTRACT } from "./contract.gen.js";
import { type SubmitOutcome, sendReport } from "./submit.js";
import { MESSAGES, type Messages, categoryLabel, ensureLocale, hasLocale, messagesFor, pickLanguage } from "./i18n.js";
import { captureWithin, screenshotStrategy } from "./capture.js";
import { applyStyles } from "./styles.js";
import { type BotGate, createBotGate } from "./botgate.js";

export const TAG = "heed-feedback";

/** Keys of the messages that are plain strings (all but `retryIn`). */
type TextKey = { [K in keyof Messages]: Messages[K] extends string ? K : never }[keyof Messages];

type Attrs = Record<string, string | boolean | undefined>;

function h<K extends keyof HTMLElementTagNameMap>(
  doc: Document,
  tag: K,
  attrs: Attrs = {},
  ...children: (Node | string)[]
): HTMLElementTagNameMap[K] {
  const el = doc.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (value === true) el.setAttribute(name, "");
    else if (typeof value === "string") el.setAttribute(name, value);
  }
  el.append(...children);
  return el;
}

/** A screenshot being taken (or taken) for the open dialog. */
interface Shot {
  /** Settles once the preview is on screen, or the capture or preview failed. */
  settled: Promise<void>;
  blob: Blob | null;
  url: string | null;
  /** The visitor has been shown the preview: only then may the image be sent. */
  shown: boolean;
}

interface Ui {
  root: HTMLDivElement;
  launcher: HTMLButtonElement;
  dialog: HTMLDialogElement;
  heading: HTMLHeadingElement;
  form: HTMLFormElement;
  categoryLabel: HTMLLabelElement;
  category: HTMLSelectElement;
  title: HTMLInputElement;
  body: HTMLTextAreaElement;
  honeypot: HTMLInputElement;
  note: HTMLParagraphElement;
  gate: HTMLDivElement;
  shotRow: HTMLDivElement;
  shotBox: HTMLInputElement;
  shotState: HTMLParagraphElement;
  preview: HTMLImageElement;
  status: HTMLParagraphElement;
  error: HTMLParagraphElement;
  submit: HTMLButtonElement;
  doneClose: HTMLButtonElement;
  done: HTMLDivElement;
  doneMessage: HTMLParagraphElement;
  doneLink: HTMLAnchorElement;
}

// Importing this module must not fail where there is no DOM (server-side rendering):
// the class then extends a stand-in and is never registered.
const Base = (typeof HTMLElement === "undefined" ? class {} : HTMLElement) as typeof HTMLElement;

export class HeedFeedbackElement extends Base {
  static get observedAttributes(): string[] {
    return OPTION_ATTRIBUTES;
  }

  #imperative: Partial<HeedOptions> = {};
  #ui: Ui | null = null;
  #sending = false;
  #shot: Shot | null = null;
  #gate: { key: string; gate: BotGate | null } | null = null;
  /** The visitor left while the send waited on its challenge: that send ends without a word. */
  #abandoned = false;
  /** What the status and error lines say (kept as a picker, not as text, to be re-said when the language changes). */
  #status: { pick: (m: Messages) => string; error: boolean } | null = null;
  /** Likewise the "done" message. */
  #done: ((m: Messages) => string) | null = null;
  /** Watches `lang` attributes above the element: a page that changes its language changes the widget's. */
  #langWatch: MutationObserver | null = null;
  /** Static texts of the UI, re-rendered when the language changes. */
  #texts: [Text, TextKey][] = [];
  /** The language whose translations were last asked for. */
  #requested = "";

  /** The effective options: defaults, then attributes, then `configure()`/properties. */
  get options(): HeedOptions {
    return { ...defaultOptions(), ...optionsFromAttributes(this), ...this.#imperative };
  }

  /** Merge options (unknown keys and bad values are dropped with a warning). */
  configure(options: Partial<HeedOptions> | Record<string, unknown>): this {
    this.#imperative = { ...this.#imperative, ...cleanOptions(options as Record<string, unknown>) };
    this.#sync();
    return this;
  }

  set botToken(provider: BotTokenProvider | null) {
    this.configure({ botToken: provider });
  }
  get botToken(): BotTokenProvider | null {
    return this.options.botToken;
  }
  set extra(provider: ExtraProvider | null) {
    this.configure({ extra: provider });
  }
  get extra(): ExtraProvider | null {
    return this.options.extra;
  }

  connectedCallback(): void {
    // A page may set botToken/extra before this script upgraded the element; those
    // landed as plain own properties that shadow the setters. Re-apply them.
    for (const prop of ["botToken", "extra"] as const) {
      if (Object.prototype.hasOwnProperty.call(this, prop)) {
        const value = (this as unknown as Record<string, unknown>)[prop];
        delete (this as unknown as Record<string, unknown>)[prop];
        (this as unknown as Record<string, unknown>)[prop] = value;
      }
    }
    this.#ui ??= this.#build();
    this.#sync();
    this.#watchLang();
  }

  disconnectedCallback(): void {
    this.#langWatch?.disconnect();
    this.#dropGate();
    // A modal dialog that leaves the document stops being modal but stays "open";
    // close it so the next open() shows it properly again.
    this.close();
  }

  attributeChangedCallback(): void {
    this.#sync();
  }

  /** Open the feedback dialog (a no-op when already open). */
  open(): void {
    if (!this.isConnected) {
      console.warn("heed: open() needs the <heed-feedback> element to be in the document");
      return;
    }
    const ui = (this.#ui ??= this.#build());
    if (!hasLocale(this.#requested)) this.#requested = ""; // the last language load failed: try again
    this.#sync();
    if (ui.dialog.open) {
      if (isModal(ui.dialog)) return;
      ui.dialog.close();
    }
    if (!ui.done.hidden) this.#showForm();
    ui.dialog.showModal();
    ui.title.focus();
    this.#warmGate();
    if (!this.#isLight()) this.#startScreenshot();
    this.#emit("heed:open", {});
  }

  /** Close the dialog. What the visitor typed is kept until it is sent. */
  close(): void {
    if (this.#ui?.dialog.open) this.#ui.dialog.close();
  }

  #build(): Ui {
    const doc = this.ownerDocument;
    const root = this.shadowRoot ?? this.attachShadow({ mode: "open" });
    applyStyles(root);
    const id = (s: string) => `heed-${s}`;
    this.#texts = [];
    const tx = (key: TextKey) => {
      const node = doc.createTextNode(MESSAGES[key]);
      this.#texts.push([node, key]);
      return node;
    };

    const launcher = h(doc, "button", { type: "button", class: "launcher", part: "launcher" });
    const heading = h(doc, "h2", { id: id("heading"), part: "heading" });
    const category = h(doc, "select", { name: "category" });
    const categoryLabel = h(doc, "label", {}, tx("categoryPrompt"), category);
    const title = h(doc, "input", { name: "title", type: "text", required: true, autocomplete: "off" });
    const body = h(doc, "textarea", { name: "body" });
    // Hints that keep password managers and autofill out of the trap field.
    const honeypot = h(doc, "input", {
      type: "text",
      tabindex: "-1",
      autocomplete: "off",
      "data-1p-ignore": true,
      "data-lpignore": "true",
      "data-bwignore": true,
      "data-form-type": "other",
    });
    const status = h(doc, "p", { class: "status", role: "status", "aria-live": "polite" });
    const error = h(doc, "p", { class: "status error", role: "alert" });
    const submit = h(doc, "button", { type: "submit", class: "primary", part: "submit" }, tx("send"));
    const cancel = h(doc, "button", { type: "button", value: "cancel" }, tx("cancel"));
    const note = h(doc, "p", { class: "note" });
    const shotBox = h(doc, "input", { type: "checkbox", checked: true });
    const shotState = h(doc, "p", { class: "note", role: "status" });
    const preview = h(doc, "img", { class: "preview", hidden: true, part: "preview" });
    const gate = h(doc, "div", { class: "gate", part: "gate" });
    const shotRow = h(doc, "div", { class: "shot" }, h(doc, "label", { class: "check" }, shotBox, tx("shotLabel")), shotState, preview);
    const form = h(
      doc,
      "form",
      { novalidate: true },
      categoryLabel,
      h(doc, "label", {}, tx("titleLabel"), title),
      h(doc, "label", {}, tx("bodyLabel"), body),
      h(doc, "div", { class: "hp", "aria-hidden": "true" }, h(doc, "label", {}, tx("honeypotLabel"), honeypot)),
      note,
      shotRow,
      gate,
      h(doc, "div", { class: "messages" }, status, error),
      h(doc, "div", { class: "actions" }, cancel, submit),
    );
    const doneMessage = h(doc, "p", { role: "status" });
    const doneLink = h(doc, "a", { target: "_blank", rel: "noopener noreferrer", hidden: true }, tx("view"));
    const doneClose = h(doc, "button", { type: "button", class: "primary" }, tx("close"));
    const done = h(doc, "div", { class: "done", hidden: true }, doneMessage, doneLink, h(doc, "div", { class: "actions" }, doneClose));
    const dialog = h(doc, "dialog", { part: "dialog", "aria-labelledby": id("heading") }, heading, form, done);
    const rootBox = h(doc, "div", { class: "root" }, launcher, dialog);
    root.append(rootBox);

    launcher.addEventListener("click", () => this.open());
    cancel.addEventListener("click", () => this.close());
    doneClose.addEventListener("click", () => this.close());
    category.addEventListener("change", () => this.#syncCapture());
    dialog.addEventListener("close", () => {
      this.#cancelToken();
      this.#emit("heed:close", {});
    });
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      void this.#submit();
    });
    title.addEventListener("input", () => {
      if (error.textContent) this.#setStatus("", false);
    });
    return { root: rootBox, launcher, dialog, heading, form, categoryLabel, category, title, body, honeypot, note, gate, shotRow, shotBox, shotState, preview, status, error, submit, doneClose, done, doneMessage, doneLink };
  }

  /** The visitor's language: `lang`, else the nearest `lang` above the element, else the browser's. */
  #language(o: HeedOptions): string {
    return pickLanguage(o.lang, this.#pageLang(), globalThis.navigator?.language);
  }

  /** The nearest `lang` above the element, looking out through the shadow roots it may sit in. */
  #pageLang(): string | null | undefined {
    // (from above the element: its own `lang` option is read separately, and an empty one means "follow the page")
    let node: Element | null = this.parentElement ?? (this.getRootNode() as ShadowRoot).host ?? null;
    for (; node; node = (node.getRootNode() as ShadowRoot).host ?? null) {
      const lang = node.closest("[lang]:not([lang=''])")?.getAttribute("lang");
      if (lang) return lang;
    }
    return null;
  }

  #watchLang(): void {
    this.#langWatch?.disconnect();
    this.#langWatch = new MutationObserver(() => this.#sync());
    for (let node: Node | undefined = this; node; node = (node.getRootNode() as ShadowRoot).host) {
      this.#langWatch.observe(node.getRootNode(), { attributes: true, attributeFilter: ["lang"], subtree: true });
    }
  }

  /** Every text, in the current language, with the embedder's overrides applied. */
  #msgs(): Messages {
    const o = this.options;
    return messagesFor(this.#language(o), o.messages);
  }

  /** Bring the rendered UI in line with the current options. */
  #sync(): void {
    const ui = this.#ui;
    if (!ui) return;
    const o = this.options;
    const lang = this.#language(o);
    const m = messagesFor(lang, o.messages);
    if (this.#requested !== lang) {
      this.#requested = lang;
      void ensureLocale(lang).then((ok) => ok && this.#sync()); // a failed load is retried by the next open()
    }
    // Screen readers should read the texts in their language, whatever the page's; unsure (English, or
    // translations still on their way): inherit the page's.
    const spoken = o.lang || (lang !== "en" && hasLocale(lang) ? lang : "");
    if (spoken) ui.root.lang = spoken;
    else ui.root.removeAttribute("lang");
    for (const [node, key] of this.#texts) node.data = m[key];
    // An explicit label or heading (any non-empty one, even the English default) beats the translation.
    ui.launcher.textContent = o.label || m.label;
    ui.launcher.className = `launcher ${o.position}`;
    ui.launcher.hidden = o.position === "none";
    ui.heading.textContent = o.heading || m.heading;
    ui.body.placeholder = m.bodyPlaceholder;
    ui.preview.alt = m.shotAlt;
    ui.title.maxLength = CONTRACT.limits.title_chars;
    ui.body.maxLength = CONTRACT.limits.body_chars;
    ui.honeypot.name = o.honeypotField || "hp";
    ui.honeypot.disabled = !o.honeypotField;
    const wanted = o.categories.map((c) => `${c}=${categoryLabel(m, c)}`).join("|"); // rebuilt when a label changes
    if (ui.category.dataset.categories !== wanted) {
      const previous = ui.category.value;
      ui.category.replaceChildren(
        ...o.categories.map((c) => h(this.ownerDocument, "option", { value: c }, categoryLabel(m, c))),
      );
      if (o.categories.includes(previous as Category)) ui.category.value = previous;
      ui.category.dataset.categories = wanted;
    }
    ui.categoryLabel.hidden = o.categories.length < 2;
    this.#syncCapture(m);
    if (this.#status) this.#setStatus(this.#status.pick, this.#status.error);
    const done = this.#done?.(m);
    if (done !== undefined && ui.doneMessage.textContent !== done) ui.doneMessage.textContent = done;
  }

  async #submit(): Promise<void> {
    const ui = this.#ui;
    if (!ui || this.#sending) return;
    const o = this.options;
    if (!ui.title.value.trim()) {
      this.#setStatus((m) => m.titleRequired, true);
      ui.title.focus();
      return;
    }
    if (!o.endpoint) {
      this.#setStatus((m) => m.noEndpoint, true);
      return;
    }
    this.#sending = true;
    this.#abandoned = false;
    ui.submit.disabled = ui.category.disabled = true; // the category decides what is sent: fixed while sending
    ui.form.setAttribute("aria-busy", "true");
    let outcome: SubmitOutcome | null = null;
    let droppedShot = false;
    try {
      const screenshot = await this.#screenshotToSend();
      if (screenshot === "review") {
        this.#setStatus((m) => m.shotReview, false);
        return;
      }
      this.#setStatus((m) => m.sending, false);
      const win = this.ownerDocument.defaultView ?? window;
      const input = {
        draft: { category: (ui.category.value || o.categories[0]) as Category, title: ui.title.value, body: ui.body.value },
        env: collectEnvironment(win, o.extra, this.#isLight()),
        pageUrl: reportPageUrl(win.location.href, o.pageUrl),
        honeypot: ui.honeypot.value,
        screenshot,
      };
      const sending = this.#withGate(o);
      outcome = await sendReport(input, sending);
      // The server's cap is lower than ours: the words matter more than the picture. A challenge needs the
      // dialog open, so with a built-in gate and a closed dialog there is no resend (the error below says so).
      if (!outcome.ok && outcome.kind === "too_large" && screenshot && (ui.dialog.open || !this.#currentGate(o))) {
        // The screenshot check comes after the token is verified, so a second challenge runs;
        // keep saying why the visitor is waiting.
        droppedShot = true;
        this.#setStatus((m) => `${m.shotDropped} ${m.sending}`, false);
        outcome = await sendReport({ ...input, screenshot: null }, this.#withGate(o, true));
      }
    } finally {
      this.#gate?.gate?.cancel(); // a token still awaited (the send timed out) is no longer wanted
      this.#sending = false;
      ui.submit.disabled = ui.category.disabled = false;
      ui.form.removeAttribute("aria-busy");
    }
    if (this.#abandoned) {
      this.#abandoned = false;
      this.#setStatus("", false);
      return;
    }
    if (!outcome) return;
    if (outcome.ok) {
      ui.form.reset();
      this.#clearScreenshot();
      const held = outcome.status === "held";
      this.#done = (m) => (held ? m.held : m.received) + (droppedShot ? ` ${m.shotDropped}` : "");
      ui.doneMessage.textContent = this.#done(this.#msgs());
      ui.doneLink.hidden = !outcome.url;
      if (outcome.url) ui.doneLink.href = outcome.url;
      else ui.doneLink.removeAttribute("href");
      ui.form.hidden = true;
      ui.done.hidden = false;
      this.#setStatus("", false);
      ui.doneClose.focus();
      this.#emit("heed:submitted", { id: outcome.id, status: outcome.status, url: outcome.url });
    } else {
      const { kind, retryAfterSeconds } = outcome;
      this.#setStatus((m) => m[kind] + (retryAfterSeconds ? ` ${safeRetryIn(m, retryAfterSeconds)}` : ""), true);
      this.#emit("heed:error", { kind: outcome.kind });
    }
  }

  /**
   * The screenshot to attach: only one the visitor has been shown. If it is still
   * being taken, wait for it, show it, and ask for a second Send ("review").
   */
  async #screenshotToSend(): Promise<Blob | null | "review"> {
    const ui = this.#ui;
    const shot = this.#shot;
    if (!ui || !this.options.screenshot || this.#isLight() || !shot || !ui.shotBox.checked) return null;
    if (shot.shown) return shot.blob;
    this.#setStatus((m) => m.shotFinishing, false);
    await shot.settled;
    // A newer capture replaced this one (the category went light and back): wait for that one.
    if (this.#shot && this.#shot !== shot) return this.#screenshotToSend();
    return shot.shown && ui.shotBox.checked && this.#shot === shot ? "review" : null;
  }

  /**
   * The built-in bot gate for the current options (null when off, misconfigured, or
   * overridden by an explicit `botToken`). Rebuilt when its options change.
   */
  #currentGate(o: HeedOptions): BotGate | null {
    const ui = this.#ui;
    if (!ui || !o.botGate || o.botToken) return this.#dropGate();
    const key = JSON.stringify([o.botGate, o.botSitekey, o.botGateScript]);
    if (this.#gate?.key !== key) {
      this.#dropGate();
      const win = this.ownerDocument.defaultView ?? window;
      this.#gate = { key, gate: createBotGate(win, ui.gate, { provider: o.botGate, sitekey: o.botSitekey, script: o.botGateScript || undefined }) };
    }
    return this.#gate!.gate;
  }

  /** Stop waiting for the challenge's token (the dialog closed, the element left the page). */
  #cancelToken(): void {
    if (this.#gate?.gate?.cancel() && this.#sending) this.#abandoned = true;
  }

  #dropGate(): null {
    this.#cancelToken();
    this.#gate?.gate?.destroy();
    this.#gate = null;
    return null;
  }

  /** Load the challenge ahead of the first send; a failure shows up as a failed send. */
  #warmGate(): void {
    this.#currentGate(this.options)?.warm().catch((e) => console.warn("heed: the bot gate could not start", e));
  }

  /** `o` with the gate's token as `botToken`; the "screenshot dropped" note (`withNote`) stays in front of the status lines while it runs. */
  #withGate(o: HeedOptions, withNote = false): HeedOptions {
    const gate = this.#currentGate(o);
    if (!gate) return o;
    const ui = this.#ui;
    return {
      ...o,
      botToken: () => {
        // An interactive challenge waits on the visitor: say so instead of "Sending…".
        const say = (pick: (m: Messages) => string) => ui && this.#setStatus((m) => (withNote ? `${m.shotDropped} ${pick(m)}` : pick(m)), false);
        say((m) => m.gateWaiting);
        return gate.token().then((token) => (say((m) => m.sending), token)); // a failure is reported by the send, not here
      },
    };
  }

  /** Does the chosen category get the light intake (no screenshot, no browser details)? */
  #isLight(): boolean {
    const o = this.options;
    const chosen = (this.#ui?.category.value || o.categories[0]) as Category;
    return o.lightCategories.includes(chosen);
  }

  /**
   * Bring the note, the screenshot row and the capture in line with the chosen category: a
   * light one shows its own note and drops the screenshot, a heavy one takes it (once).
   * Runs at the end of every sync, so a changed `categories` or `lightCategories` is covered.
   */
  #syncCapture(m: Messages = this.#msgs()): void {
    const ui = this.#ui;
    if (!ui) return;
    const o = this.options;
    const light = this.#isLight();
    ui.note.textContent = light ? m.noteLight : o.screenshot ? m.noteShot : m.note;
    ui.shotRow.hidden = !o.screenshot || light;
    if (light) {
      if (this.#shot) this.#clearScreenshot();
    } else if (o.screenshot && ui.dialog.open && !this.#shot) this.#startScreenshot();
  }

  /** Capture the page behind the dialog, in the background; the visitor sees a preview. */
  #startScreenshot(): void {
    const ui = this.#ui;
    this.#clearScreenshot();
    const o = this.options;
    if (!ui || !o.screenshot) return;
    ui.shotBox.checked = true;
    ui.shotBox.disabled = false;
    ui.shotState.textContent = this.#msgs().shotTaking;
    const win = this.ownerDocument.defaultView ?? window;
    const shot: Shot = { settled: Promise.resolve(), blob: null, url: null, shown: false };
    const fail = (message: string) => {
      ui.shotBox.checked = false;
      ui.shotBox.disabled = true;
      ui.preview.hidden = true;
      ui.shotState.textContent = message;
    };
    // Let the dialog paint before the (main-thread) capture starts.
    shot.settled = new Promise((r) => setTimeout(r, 50))
      .then(() =>
        captureWithin(
          o.takeScreenshot ?? screenshotStrategy(),
          win,
          { maskInputs: o.maskInputs, maskSelector: o.maskSelector, maxNodes: o.screenshotMaxNodes },
          o.screenshotTimeoutMs,
        ),
      )
      .then(
        (blob) =>
          new Promise<void>((done) => {
            if (this.#shot !== shot) return done(); // a newer capture (or a reset) replaced this one
            if (!blob) {
              fail(this.#msgs().shotFailed);
              return done();
            }
            shot.blob = blob;
            shot.url = URL.createObjectURL(blob);
            const settle = (ok: boolean) => {
              ui.preview.onload = ui.preview.onerror = null;
              if (this.#shot === shot) {
                if (ok) {
                  shot.shown = true;
                  ui.shotState.textContent = "";
                } else fail(this.#msgs().shotNoPreview); // e.g. a CSP without img-src blob:
              }
              done();
            };
            ui.preview.onload = () => settle(true);
            ui.preview.onerror = () => settle(false);
            ui.preview.src = shot.url;
            ui.preview.hidden = false;
          }),
      );
    this.#shot = shot;
  }

  #clearScreenshot(): void {
    if (this.#shot?.url) URL.revokeObjectURL(this.#shot.url);
    this.#shot = null;
    if (!this.#ui) return;
    this.#ui.preview.hidden = true;
    this.#ui.preview.removeAttribute("src");
    this.#ui.shotState.textContent = "";
  }

  #showForm(): void {
    if (!this.#ui) return;
    this.#ui.form.hidden = false;
    this.#ui.done.hidden = true;
    this.#done = null;
  }

  /** Progress goes to a polite status region, failures to an alert region. */
  #setStatus(message: string | ((m: Messages) => string), isError: boolean): void {
    if (!this.#ui) return;
    const pick = typeof message === "string" ? () => message : message;
    this.#status = message === "" ? null : { pick, error: isError };
    const text = message === "" ? "" : pick(this.#msgs());
    // (only when it changed: a role="alert" line rewritten with the same text may be announced again)
    const [status, error] = isError ? ["", text] : [text, ""];
    if (this.#ui.status.textContent !== status) this.#ui.status.textContent = status;
    if (this.#ui.error.textContent !== error) this.#ui.error.textContent = error;
  }

  #emit(type: string, detail: unknown): void {
    this.dispatchEvent(new CustomEvent(type, { detail, bubbles: true, composed: true }));
  }
}

/** The embedder's `retryIn`, which may throw: the report's failure is still reported, with the English hint. */
function safeRetryIn(m: Messages, seconds: number): string {
  try {
    const text = m.retryIn(seconds);
    if (typeof text === "string") return text;
  } catch (e) {
    console.warn("heed: the retryIn message threw", e);
  }
  return MESSAGES.retryIn(seconds);
}

function isModal(dialog: HTMLDialogElement): boolean {
  try {
    return dialog.matches(":modal");
  } catch {
    return true; // a browser without :modal: trust the open flag
  }
}

/** Register `<heed-feedback>` once (loading the bundle twice is harmless). */
export function defineHeedElement(registry?: CustomElementRegistry): void {
  const reg = registry ?? (typeof customElements === "undefined" ? undefined : customElements);
  if (!reg) return; // no DOM (server-side rendering)
  const existing = reg.get(TAG);
  if (!existing) reg.define(TAG, HeedFeedbackElement);
  else if (existing !== HeedFeedbackElement) {
    console.warn(
      "heed: <heed-feedback> was already defined by another copy of heed (the served script, or another version); that copy is the one in use",
    );
  }
}
