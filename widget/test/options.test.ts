import { describe, expect, it, vi } from "vitest";
import { cleanOptions, defaultOptions, optionsFromAttributes, OPTION_ATTRIBUTES } from "../src/options";
import { CONTRACT } from "../src/contract.gen";

describe("options", () => {
  it("defaults follow the server contract", () => {
    const o = defaultOptions();
    expect(o.categories).toEqual([...CONTRACT.categories]);
    expect(o.botTokenField).toBe(CONTRACT.fields.bot_token);
    expect(o.honeypotField).toBe(CONTRACT.fields.honeypot);
    expect(o.pageUrl).toBe("path");
    expect(o.withCredentials).toBe(false);
  });

  it("parses attributes and skips unparsable ones", () => {
    const el = document.createElement("div");
    el.setAttribute("endpoint", " /heed/report ");
    el.setAttribute("position", "top-middle");
    el.setAttribute("categories", "feature, bogus bug feature");
    el.setAttribute("with-credentials", "");
    el.setAttribute("timeout-ms", "-5");
    expect(optionsFromAttributes(el)).toEqual({ endpoint: "/heed/report", categories: ["feature", "bug"], withCredentials: true });
  });

  it("observes exactly the attributes the table declares", () => {
    expect(OPTION_ATTRIBUTES).toContain("endpoint");
    expect(OPTION_ATTRIBUTES).not.toContain("botToken");
  });

  it("cleanOptions keeps valid values and drops junk with a warning", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const fn = () => "tok";
    const out = cleanOptions({ endpoint: "/r", position: "nowhere", timeoutMs: 0, botToken: fn, typo: 1, categories: ["question"], label: 3 });
    expect(out).toEqual({ endpoint: "/r", botToken: fn, categories: ["question"] });
    expect(warn).toHaveBeenCalledTimes(3);
    warn.mockRestore();
  });
  it("drops an anon-id the server would refuse", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const el = document.createElement("div");
    el.setAttribute("anon-id", "me@x.com");
    expect(optionsFromAttributes(el)).toEqual({});
    el.setAttribute("anon-id", "visitor_42:a.b-c");
    expect(optionsFromAttributes(el)).toEqual({ anonId: "visitor_42:a.b-c" });
    expect(cleanOptions({ anonId: "x".repeat(500) })).toEqual({});
    warn.mockRestore();
  });
  it("caps timeouts at what setTimeout can wait", () => {
    expect(cleanOptions({ timeoutMs: 3_000_000_000 })).toEqual({ timeoutMs: 2_147_483_647 });
  });
});
