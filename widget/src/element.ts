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
import { collectEnvironment, reportPageUrl } from "./env";
import {
  type BotTokenProvider,
  type Category,
  type ExtraProvider,
  type HeedOptions,
  OPTION_ATTRIBUTES,
  cleanOptions,
  defaultOptions,
  optionsFromAttributes,
} from "./options";
import { CONTRACT } from "./contract.gen";
import { MESSAGES, type SubmitOutcome, sendReport } from "./submit";
import { applyStyles } from "./styles";

export const TAG = "heed-feedback";

const CATEGORY_LABELS: Record<Category, string> = {
  bug: "Something is broken",
  feature: "An idea or request",
  question: "A question",
  other: "Something else",
};

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

interface Ui {
  launcher: HTMLButtonElement;
  dialog: HTMLDialogElement;
  heading: HTMLHeadingElement;
  form: HTMLFormElement;
  categoryLabel: HTMLLabelElement;
  category: HTMLSelectElement;
  title: HTMLInputElement;
  body: HTMLTextAreaElement;
  honeypot: HTMLInputElement;
  status: HTMLParagraphElement;
  error: HTMLParagraphElement;
  submit: HTMLButtonElement;
  doneClose: HTMLButtonElement;
  done: HTMLDivElement;
  doneMessage: HTMLParagraphElement;
  doneLink: HTMLAnchorElement;
}

export class HeedFeedbackElement extends HTMLElement {
  static get observedAttributes(): string[] {
    return OPTION_ATTRIBUTES;
  }

  #imperative: Partial<HeedOptions> = {};
  #ui: Ui | null = null;
  #sending = false;

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
  }

  disconnectedCallback(): void {
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
    this.#sync();
    if (ui.dialog.open) {
      if (isModal(ui.dialog)) return;
      ui.dialog.close();
    }
    if (!ui.done.hidden) this.#showForm();
    ui.dialog.showModal();
    ui.title.focus();
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

    const launcher = h(doc, "button", { type: "button", class: "launcher", part: "launcher" });
    const heading = h(doc, "h2", { id: id("heading"), part: "heading" });
    const category = h(doc, "select", { name: "category" });
    const categoryLabel = h(doc, "label", {}, "What is it about?", category);
    const title = h(doc, "input", { name: "title", type: "text", required: true, autocomplete: "off" });
    const body = h(doc, "textarea", { name: "body", placeholder: "What happened, and what did you expect?" });
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
    const submit = h(doc, "button", { type: "submit", class: "primary", part: "submit" }, "Send");
    const cancel = h(doc, "button", { type: "button", value: "cancel" }, "Cancel");
    const form = h(
      doc,
      "form",
      { novalidate: true },
      categoryLabel,
      h(doc, "label", {}, "Title", title),
      h(doc, "label", {}, "Details", body),
      h(doc, "div", { class: "hp", "aria-hidden": "true" }, h(doc, "label", {}, "Leave this field empty", honeypot)),
      h(doc, "p", { class: "note" }, "Sent with your report: this page's address (without its query) and your browser details."),
      h(doc, "div", { class: "messages" }, status, error),
      h(doc, "div", { class: "actions" }, cancel, submit),
    );
    const doneMessage = h(doc, "p", { role: "status" });
    const doneLink = h(doc, "a", { target: "_blank", rel: "noopener noreferrer", hidden: true }, MESSAGES.view);
    const doneClose = h(doc, "button", { type: "button", class: "primary" }, "Close");
    const done = h(doc, "div", { class: "done", hidden: true }, doneMessage, doneLink, h(doc, "div", { class: "actions" }, doneClose));
    const dialog = h(doc, "dialog", { part: "dialog", "aria-labelledby": id("heading") }, heading, form, done);
    root.append(h(doc, "div", { class: "root" }, launcher, dialog));

    launcher.addEventListener("click", () => this.open());
    cancel.addEventListener("click", () => this.close());
    doneClose.addEventListener("click", () => this.close());
    dialog.addEventListener("close", () => this.#emit("heed:close", {}));
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      void this.#submit();
    });
    title.addEventListener("input", () => {
      if (error.textContent) this.#setStatus("", false);
    });
    return { launcher, dialog, heading, form, categoryLabel, category, title, body, honeypot, status, error, submit, doneClose, done, doneMessage, doneLink };
  }

  /** Bring the rendered UI in line with the current options. */
  #sync(): void {
    const ui = this.#ui;
    if (!ui) return;
    const o = this.options;
    ui.launcher.textContent = o.label;
    ui.launcher.className = `launcher ${o.position}`;
    ui.launcher.hidden = o.position === "none";
    ui.heading.textContent = o.heading;
    ui.title.maxLength = CONTRACT.limits.title_chars;
    ui.body.maxLength = CONTRACT.limits.body_chars;
    ui.honeypot.name = o.honeypotField || "hp";
    ui.honeypot.disabled = !o.honeypotField;
    const wanted = o.categories.join(",");
    if (ui.category.dataset.categories !== wanted) {
      const previous = ui.category.value;
      ui.category.replaceChildren(
        ...o.categories.map((c) => h(this.ownerDocument, "option", { value: c }, CATEGORY_LABELS[c])),
      );
      if (o.categories.includes(previous as Category)) ui.category.value = previous;
      ui.category.dataset.categories = wanted;
    }
    ui.categoryLabel.hidden = o.categories.length < 2;
  }

  async #submit(): Promise<void> {
    const ui = this.#ui;
    if (!ui || this.#sending) return;
    const o = this.options;
    if (!ui.title.value.trim()) {
      this.#setStatus(MESSAGES.titleRequired, true);
      ui.title.focus();
      return;
    }
    if (!o.endpoint) {
      this.#setStatus(MESSAGES.noEndpoint, true);
      return;
    }
    this.#sending = true;
    ui.submit.disabled = true;
    ui.form.setAttribute("aria-busy", "true");
    this.#setStatus(MESSAGES.sending, false);
    let outcome: SubmitOutcome;
    try {
      const win = this.ownerDocument.defaultView ?? window;
      outcome = await sendReport(
        {
          draft: { category: (ui.category.value || o.categories[0]) as Category, title: ui.title.value, body: ui.body.value },
          env: collectEnvironment(win, o.extra),
          pageUrl: reportPageUrl(win.location.href, o.pageUrl),
          honeypot: ui.honeypot.value,
        },
        o,
      );
    } finally {
      this.#sending = false;
      ui.submit.disabled = false;
      ui.form.removeAttribute("aria-busy");
    }
    if (outcome.ok) {
      ui.form.reset();
      ui.doneMessage.textContent = outcome.status === "held" ? MESSAGES.held : MESSAGES.received;
      ui.doneLink.hidden = !outcome.url;
      if (outcome.url) ui.doneLink.href = outcome.url;
      else ui.doneLink.removeAttribute("href");
      ui.form.hidden = true;
      ui.done.hidden = false;
      this.#setStatus("", false);
      ui.doneClose.focus();
      this.#emit("heed:submitted", { id: outcome.id, status: outcome.status, url: outcome.url });
    } else {
      const retry = outcome.retryAfterSeconds ? ` ${MESSAGES.retryIn(outcome.retryAfterSeconds)}` : "";
      this.#setStatus(MESSAGES[outcome.kind] + retry, true);
      this.#emit("heed:error", { kind: outcome.kind });
    }
  }

  #showForm(): void {
    if (!this.#ui) return;
    this.#ui.form.hidden = false;
    this.#ui.done.hidden = true;
  }

  /** Progress goes to a polite status region, failures to an alert region. */
  #setStatus(message: string, isError: boolean): void {
    if (!this.#ui) return;
    this.#ui.status.textContent = isError ? "" : message;
    this.#ui.error.textContent = isError ? message : "";
  }

  #emit(type: string, detail: unknown): void {
    this.dispatchEvent(new CustomEvent(type, { detail, bubbles: true, composed: true }));
  }
}

function isModal(dialog: HTMLDialogElement): boolean {
  try {
    return dialog.matches(":modal");
  } catch {
    return true; // a browser without :modal: trust the open flag
  }
}

/** Register `<heed-feedback>` once (loading the bundle twice is harmless). */
export function defineHeedElement(registry: CustomElementRegistry = customElements): void {
  if (!registry.get(TAG)) registry.define(TAG, HeedFeedbackElement);
}
