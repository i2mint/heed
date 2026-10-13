import { afterEach, describe, expect, it, vi } from "vitest";
import { act, createElement, createRef } from "react";
import { createRoot, type Root } from "react-dom/client";
import { HeedFeedback, type HeedFeedbackHandle } from "../src/react";
import { TAG, type HeedFeedbackElement } from "../src/element";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
const container = () => document.body.appendChild(document.createElement("div"));
const render = (props: Record<string, unknown>, ref?: React.Ref<HeedFeedbackHandle>) =>
  act(() => {
    root ??= createRoot(container());
    root.render(createElement(HeedFeedback, { ...props, ref }));
  });
const element = () => document.querySelector(TAG) as HeedFeedbackElement;

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.replaceChildren();
  vi.unstubAllGlobals();
});

describe("<HeedFeedback>", () => {
  it("renders the custom element with its options, functions included", () => {
    const botToken = () => "tok";
    render({ endpoint: "/r", label: "Report", position: "bottom-left", botToken, screenshot: false, className: "mine", style: { "--heed-accent": "#0a7" } });
    const el = element();
    expect(el).toBeTruthy();
    expect(el.options).toMatchObject({ endpoint: "/r", label: "Report", position: "bottom-left", screenshot: false });
    expect(el.options.botToken).toBe(botToken);
    expect(el.getAttribute("class")).toBe("mine");
    expect(el.style.getPropertyValue("--heed-accent")).toBe("#0a7");
    expect(el.shadowRoot!.querySelector(".launcher")!.textContent).toBe("Report");
  });

  it("follows prop changes, and a removed prop goes back to its default", () => {
    render({ endpoint: "/r", label: "One" });
    expect(element().options.label).toBe("One");
    render({ endpoint: "/r", label: "Two" });
    expect(element().options.label).toBe("Two");
    render({ endpoint: "/r" });
    expect(element().options.label).toBe("");
  });

  it("messages follow the prop and are cleared when it is removed", () => {
    const send = () => element().shadowRoot!.querySelector("button[type=submit]")!.textContent;
    render({ endpoint: "/r", messages: { send: "Go" } });
    expect(send()).toBe("Go");
    render({ endpoint: "/r", messages: { cancel: "Nope" } });
    expect(send()).toBe("Send");
    render({ endpoint: "/r", messages: { send: "Go" } });
    render({ endpoint: "/r" });
    expect(send()).toBe("Send");
  });

  it("opens and closes through a ref, and reports events through callbacks", () => {
    const ref = createRef<HeedFeedbackHandle>();
    const onOpen = vi.fn();
    const onClose = vi.fn();
    render({ endpoint: "/r", screenshot: false, onOpen, onClose }, ref);
    act(() => ref.current!.open());
    expect(ref.current!.element!.shadowRoot!.querySelector("dialog")!.open).toBe(true);
    act(() => ref.current!.close());
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("calls the latest onSubmitted with the report", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ id: "r9", ok: true, status: "received", url: null }))));
    const first = vi.fn();
    const latest = vi.fn();
    const ref = createRef<HeedFeedbackHandle>();
    render({ endpoint: "/r", screenshot: false, onSubmitted: first }, ref);
    render({ endpoint: "/r", screenshot: false, onSubmitted: latest }, ref);
    const el = ref.current!.element!;
    act(() => el.open());
    (el.shadowRoot!.querySelector("input[name=title]") as HTMLInputElement).value = "from react";
    await act(async () => {
      el.shadowRoot!.querySelector("form")!.requestSubmit();
      await vi.waitFor(() => expect(latest).toHaveBeenCalled());
    });
    expect(latest).toHaveBeenCalledWith({ id: "r9", status: "received", url: null });
    expect(first).not.toHaveBeenCalled();
  });

  it("uses the lazily imported rasterizer by default when bundled", async () => {
    const { screenshotStrategy } = await import("../src/capture");
    const { moduleScreenshot } = await import("../src/lib");
    expect(screenshotStrategy()).toBe(moduleScreenshot);
  });
  it("forwards id, title, data-* and aria-* to the element", () => {
    render({ endpoint: "/r", id: "fb", title: "Feedback", "data-testid": "x", "aria-label": "Send feedback" });
    const el = element();
    expect([el.id, el.title, el.dataset.testid, el.getAttribute("aria-label")]).toEqual(["fb", "Feedback", "x", "Send feedback"]);
    render({ endpoint: "/r" });
    expect([el.hasAttribute("id"), el.hasAttribute("title"), el.hasAttribute("data-testid")]).toEqual([false, false, false]);
  });

  it("re-applies only changed props, so a bad value warns once", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    render({ endpoint: "/r", label: 5 });
    render({ endpoint: "/r", label: 5 });
    render({ endpoint: "/r", label: 5, heading: "x" });
    expect(warn.mock.calls.filter((c) => String(c[0]).includes("label"))).toHaveLength(1);
    warn.mockRestore();
  });
});

describe("defineHeedElement", () => {
  it("warns when another copy of heed defined the element first", async () => {
    const { defineHeedElement } = await import("../src/element");
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const registry = { get: () => class extends HTMLElement {}, define: vi.fn() } as unknown as CustomElementRegistry;
    defineHeedElement(registry);
    expect(warn).toHaveBeenCalledWith(expect.stringContaining("another copy"));
    warn.mockRestore();
  });
});
