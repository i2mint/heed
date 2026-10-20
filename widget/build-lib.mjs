// Build the widget as an ES module package for apps that bundle it (`npm pack` runs
// this): dist/lib.js (registers <heed-feedback>), dist/react.js (the React wrapper),
// a lazily imported rasterizer chunk, and type declarations. React stays external.
// The served files (heed/static/widget/) come from build.mjs instead.
import { build } from "esbuild";
import { execFileSync } from "node:child_process";
import { readFileSync, rmSync } from "node:fs";
import { licence } from "./licence.mjs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const pkg = JSON.parse(readFileSync(join(here, "package.json"), "utf8"));
const outdir = process.argv[2] ?? join(here, "dist");

rmSync(outdir, { recursive: true, force: true });
await build({
  entryPoints: { lib: join(here, "src/lib.ts"), react: join(here, "src/react.ts") },
  bundle: true,
  splitting: true,
  format: "esm",
  target: ["es2020"],
  outdir,
  chunkNames: "chunks/[name]-[hash]",
  external: ["react", "react-dom"],
  minify: true,
  legalComments: "inline",
  // The rasterizer chunk includes modern-screenshot (MIT), whose licence travels in
  // every file; "use client" lets React Server Components import the entries.
  banner: { js: `"use client";\n${licence("modern-screenshot")}` },
  define: {
    __HEED_VERSION__: JSON.stringify(pkg.version),
    // Only the served bundle loads the rasterizer by URL; here it is imported.
    __HEED_RASTER__: JSON.stringify(""),
    __HEED_RASTER_INTEGRITY__: JSON.stringify(""),
    // Likewise the translations: imported lazily, not fetched by URL.
    __HEED_LOCALES__: JSON.stringify(""),
    __HEED_LOCALES_INTEGRITY__: JSON.stringify(""),
    // And the console/network recorder.
    __HEED_RECORDER__: JSON.stringify(""),
    __HEED_RECORDER_INTEGRITY__: JSON.stringify(""),
  },
});
if (!process.argv[2]) {
  execFileSync(join(here, "node_modules", ".bin", "tsc"), ["-p", join(here, "tsconfig.lib.json")], { stdio: "inherit" });
}
console.log(`built ${outdir}`);
