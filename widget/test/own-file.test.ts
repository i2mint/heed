import { describe, expect, it } from "vitest";
import loader from "../src/loader.ts?raw";
import { OWN_FILE } from "../src/sibling";

describe("the names of heed's own files", () => {
  it("the loader (a separate bundle that cannot import it) lists the same ones as sibling.ts", () => {
    const written = /\/(\^heed-\([^)]*\)-\[0-9a-f\]\{10\}\\\.js\$)\//.exec(loader);
    expect(written, "the loader's file-name pattern").not.toBeNull();
    expect(written![1]).toBe(OWN_FILE.source);
  });
});
