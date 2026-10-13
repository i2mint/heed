import { describe, expect, it, vi } from "vitest";
import { collectEnvironment, collectExtra, reportPageUrl } from "../src/env";
import { CONTRACT } from "../src/contract.gen";

describe("reportPageUrl", () => {
  it("drops query, fragment and credentials by default", () => {
    expect(reportPageUrl("https://u:p@a.example/x/y?token=s#frag", "path")).toBe("https://a.example/x/y");
  });
  it("keeps the query in full mode, still without credentials", () => {
    expect(reportPageUrl("https://u:p@a.example/x?q=1#f", "full")).toBe("https://a.example/x?q=1#f");
  });
  it("sends nothing for pages the server would refuse", () => {
    expect(reportPageUrl("file:///etc/passwd", "full")).toBe("");
    expect(reportPageUrl("about:blank", "path")).toBe("");
    expect(reportPageUrl("not a url", "path")).toBe("");
  });
  it("never exceeds the server's URL limit", () => {
    const long = "https://a.example/p?" + "q".repeat(5000);
    expect(reportPageUrl(long, "full")).toBe("https://a.example/p");
    const longPath = "https://a.example/" + "p".repeat(5000);
    expect(reportPageUrl(longPath, "path")).toBe("https://a.example/");
  });
});

describe("environment", () => {
  it("collects viewport, locale and dpr", () => {
    const env = collectEnvironment(window, () => ({ app: "1.2" }));
    expect(env.viewport).toMatch(/^\d+x\d+$/);
    expect(env.user_agent).toBeTruthy();
    expect(env.extra).toEqual({ app: "1.2" });
  });
  it("caps extra facts to the server's limits and survives a throwing provider", () => {
    const many = Object.fromEntries(Array.from({ length: 50 }, (_, i) => [`k${i}`.padEnd(100, "x"), "v".repeat(900)]));
    const out = collectExtra(() => many);
    expect(Object.keys(out)).toHaveLength(CONTRACT.limits.extra_items);
    for (const [k, v] of Object.entries(out)) {
      expect(k.length).toBeLessThanOrEqual(CONTRACT.limits.extra_key_chars);
      expect(v.length).toBeLessThanOrEqual(CONTRACT.limits.short_chars);
    }
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(collectExtra(() => { throw new Error("x"); })).toEqual({});
    warn.mockRestore();
  });
});
