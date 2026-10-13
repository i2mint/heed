import { afterEach, describe, expect, it, vi } from "vitest";
import "../src/index";
import { HeedFeedbackElement, TAG } from "../src/element";
import { LANGUAGES, MESSAGES, cleanMessages, messagesFor, pickLanguage, setLocaleLoader } from "../src/i18n";
import { LOCALES } from "../src/locales";
import { CONTRACT } from "../src/contract.gen";

const mount = (attrs: Record<string, string> = {}) => {
  const el = document.createElement(TAG) as HeedFeedbackElement;
  for (const [k, v] of Object.entries({ endpoint: "https://h.example/r", screenshot: "false", ...attrs })) el.setAttribute(k, v);
  document.body.append(el);
  return el;
};
const $ = (el: HeedFeedbackElement, sel: string) => el.shadowRoot!.querySelector(sel) as HTMLElement;
const nextTick = () => new Promise((r) => setTimeout(r, 0));

afterEach(() => {
  document.body.replaceChildren();
  document.documentElement.removeAttribute("lang");
  setLocaleLoader(async (lang) => LOCALES[lang] ?? null);
  vi.restoreAllMocks();
});

describe("the translation tables", () => {
  it("English has a label for every category the contract offers", () => {
    for (const c of CONTRACT.categories) expect(MESSAGES).toHaveProperty(`cat_${c}`);
  });

  it.each(Object.keys(LOCALES))("%s translates only known keys, with the right types, and covers them all", (lang) => {
    const table = LOCALES[lang] as Record<string, unknown>;
    for (const [key, value] of Object.entries(table)) {
      expect(MESSAGES, `${lang}.${key}`).toHaveProperty(key);
      expect(typeof value).toBe(typeof (MESSAGES as Record<string, unknown>)[key]);
    }
    expect(Object.keys(table).sort()).toEqual(Object.keys(MESSAGES).sort());
    expect((LOCALES[lang].retryIn as (s: number) => string)(5)).toMatch(/5/);
  });

  it("every built-in language but English has a table", () => {
    expect(LANGUAGES.filter((l) => l !== "en").sort()).toEqual(Object.keys(LOCALES).sort());
  });
});

describe("pickLanguage", () => {
  it("takes the first non-empty tag; its base language decides", () => {
    expect(pickLanguage("fr-CA", "de")).toBe("fr");
    expect(pickLanguage("", null, "pt_BR")).toBe("pt");
    expect(pickLanguage(undefined, "ES")).toBe("es");
  });
  it("English for unsupported or no language, without falling through to the next tag", () => {
    expect(pickLanguage("ja", "fr")).toBe("en");
    expect(pickLanguage("", undefined)).toBe("en");
  });
});

describe("messages", () => {
  it("cleanMessages keeps known keys of the right type and drops the rest", () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(cleanMessages({ send: "Go", retryIn: (s: number) => `${s}s`, nope: "x", cancel: 3 })).toEqual({ send: "Go", retryIn: expect.any(Function) });
    expect(cleanMessages(JSON.parse('{"constructor": "x", "toString": "y", "send": "Go"}'))).toEqual({ send: "Go" });
    expect(cleanMessages("x")).toBeUndefined();
    expect(cleanMessages(["a"])).toBeUndefined();
  });

  it("layers English, the language, then the overrides", () => {
    expect(messagesFor("en", null).send).toBe("Send");
    expect(messagesFor("fr", null)).toBeDefined();
    expect(messagesFor("en", { send: "Go" }).send).toBe("Go");
  });
});

describe("<heed-feedback> language", () => {
  it("is English by default and overrides single texts from code", () => {
    const el = mount();
    expect($(el, "button[type=submit]").textContent).toBe("Send");
    el.configure({ messages: { send: "Go", cat_bug: "Broken" } });
    expect($(el, "button[type=submit]").textContent).toBe("Go");
    expect(($(el, "select") as HTMLSelectElement).options[0].textContent).toBe("Broken");
  });

  it("relabels any category after mount, not just the first, and messages accumulate across calls", () => {
    const el = mount();
    el.configure({ messages: { cat_question: "Q?" } });
    el.configure({ messages: { cat_feature: "IDEA" } });
    const labels = Array.from(($(el, "select") as HTMLSelectElement).options).map((o) => o.textContent);
    expect(labels).toContain("Q?");
    expect(labels).toContain("IDEA");
    el.configure({ messages: { send: "Go" } });
    expect($(el, "button[type=submit]").textContent).toBe("Go");
    expect(Array.from(($(el, "select") as HTMLSelectElement).options).map((o) => o.textContent)).toContain("Q?");
  });

  it("marks its content with the language it speaks", async () => {
    const el = mount({ lang: "de" });
    expect($(el, ".root").getAttribute("lang")).toBe("de");
    el.setAttribute("lang", "ja");
    expect($(el, ".root").getAttribute("lang")).toBe("en");
  });

  it("retries a failed translation download on the next open, not on every update", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const loader = vi.fn(async () => {
      throw new Error("offline");
    });
    setLocaleLoader(loader);
    const el = mount({ lang: "pt" });
    await vi.waitFor(() => expect(loader).toHaveBeenCalledTimes(1));
    el.configure({ heading: "A" });
    el.configure({ heading: "B" });
    await nextTick();
    expect(loader).toHaveBeenCalledTimes(1);
    el.open();
    await vi.waitFor(() => expect(loader).toHaveBeenCalledTimes(2));
  });

  it("follows the lang attribute, loading the translations on demand", async () => {
    const loader = vi.fn(async (lang: string) => LOCALES[lang] ?? null);
    setLocaleLoader(loader);
    const el = mount({ lang: "fr" });
    expect($(el, "button[type=submit]").textContent).toBe("Send"); // English until they arrive
    await vi.waitFor(() => expect($(el, "button[type=submit]").textContent).toBe("Envoyer"));
    expect($(el, ".launcher").textContent).toBe(LOCALES.fr.label);
    expect($(el, "dialog h2").textContent).toBe(LOCALES.fr.heading);
    expect($(el, "textarea").getAttribute("placeholder")).toBe(LOCALES.fr.bodyPlaceholder);
    expect(($(el, "select") as HTMLSelectElement).options[0].textContent).toBe(LOCALES.fr.cat_bug);
    expect(loader).toHaveBeenCalledTimes(1);
    el.setAttribute("lang", "de");
    await vi.waitFor(() => expect($(el, "button[type=submit]").textContent).toBe("Senden"));
  });

  it("follows the page's lang when the element has none", async () => {
    document.documentElement.lang = "es";
    const el = mount();
    await vi.waitFor(() => expect($(el, "button[type=submit]").textContent).toBe("Enviar"));
  });

  it("stays English, without loading anything, for English or an unsupported language", async () => {
    const loader = vi.fn(async () => null);
    setLocaleLoader(loader);
    mount({ lang: "en" });
    mount({ lang: "ja" });
    await nextTick();
    expect(loader).not.toHaveBeenCalled();
  });

  it("keeps English and says so when the translations cannot be loaded", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    setLocaleLoader(async () => {
      throw new Error("offline");
    });
    const el = mount({ lang: "pt" });
    await vi.waitFor(() => expect(warn).toHaveBeenCalled());
    expect($(el, "button[type=submit]").textContent).toBe("Send");
  });

  it("an explicit label or heading beats the translation; messages beat the translation", async () => {
    const el = mount({ lang: "fr", label: "Bug ?" });
    await vi.waitFor(() => expect($(el, "button[type=submit]").textContent).toBe("Envoyer"));
    expect($(el, ".launcher").textContent).toBe("Bug ?");
    el.configure({ messages: { send: "Go" } });
    expect($(el, "button[type=submit]").textContent).toBe("Go");
  });

  it("answers the visitor in their language (errors, with the retry hint)", async () => {
    const el = mount({ lang: "fr" });
    await vi.waitFor(() => expect($(el, "button[type=submit]").textContent).toBe("Envoyer"));
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{}", { status: 429, headers: { "Retry-After": "1" } })),
    );
    ($(el, "input[name=title]") as HTMLInputElement).value = "t";
    ($(el, "form") as HTMLFormElement).requestSubmit();
    await vi.waitFor(() => expect($(el, ".status.error").textContent).toContain(LOCALES.fr.rate_limited));
    expect($(el, ".status.error").textContent).toContain("1 seconde");
    vi.unstubAllGlobals();
  });
});
