import { afterEach, describe, expect, it, vi } from "vitest";
import { ensureRecording, recorded, setRecorderLoader, stopRecording } from "../src/record";

type Reg = { early?: { stop(): void }; ignored: Set<string> };
const w = window as unknown as { __heedRecorder?: Reg; __heedEarly?: object };

afterEach(() => {
  w.__heedRecorder?.early?.stop();
  delete w.__heedRecorder;
  delete w.__heedEarly;
  stopRecording(window);
  vi.resetModules();
});

describe("capture started by the loader (heed#62)", () => {
  it("records from the first message, masked, and the bundle adopts that recorder", async () => {
    w.__heedEarly = { console: true, network: false };
    await import("../src/recorder-entry");
    console.warn("early for ana@example.com");
    expect(w.__heedRecorder?.early).toBeDefined();

    setRecorderLoader(async () => w.__heedRecorder as never);
    ensureRecording(window, { console: true, network: false }, "https://x.example/report");
    await Promise.resolve();
    await Promise.resolve();
    const logs = recorded(window, { console: true, network: false }).console!;
    expect(logs.map((l) => l.message)).toEqual(["early for [email]"]);
    expect(w.__heedRecorder!.ignored.has("https://x.example/report")).toBe(true);
  });

  it("without the loader's request the entry only registers", async () => {
    await import("../src/recorder-entry");
    expect(w.__heedRecorder?.early).toBeUndefined();
  });

  it("a second load keeps the first recorder", async () => {
    w.__heedEarly = { console: true, network: false };
    await import("../src/recorder-entry");
    const first = w.__heedRecorder;
    vi.resetModules();
    await import("../src/recorder-entry");
    expect(w.__heedRecorder).toBe(first);
  });
});
