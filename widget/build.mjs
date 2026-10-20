// Build the widget into heed/static/widget/, the folder the Python package ships and
// heed.widget.make_widget_router serves. Each entry is written twice: under a
// content-hashed name, cached forever and meant for pinning with SRI, and under a
// stable name (`<stem>.js`, short cache) that keeps working across heed upgrades.
// manifest.json lists every file with its SRI hash and size.
//
// Hashed files of the previous RETAIN_VERSIONS versions (widget/package.json) stay
// published, so a loader cached before an upgrade, a backend replica still on the old
// version, or a site that pinned a build with SRI keeps working. Rebuilding without
// a version bump replaces that version's files: bump the version for every widget
// change that ships.
// Fails when a file outgrows its gzip budget: the widget loads on other people's sites.
import { build } from "esbuild";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";
import { licence } from "./licence.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const OUT_DIR = join(here, "..", "heed", "static", "widget");
const pkg = JSON.parse(readFileSync(join(here, "package.json"), "utf8"));
// Gzipped-size budgets in bytes, per entry (widget: 10_000 plus the opt-in bot-gate adapter, heed#37, the i18n machinery, heed#39, the hCaptcha entry and gate cancel/visibility, heed#43, and the language re-rendering, heed#45, and the screenshot cancel signal, heed#38, and opt-in capture: its fields, consent box and recorder hookup, heed#7 (the recorder itself is a separate lazy file); translations are a separate lazy file; loader: 1_500 since it starts the recorder early when capture is on, heed#62; recorder: 3_850 for masking relative URLs given no base, heed#69).
const BUDGETS = { raster: 14_000, locales: 5_000, recorder: 3_850, widget: 13_400, loader: 1_500 };
const RETAIN_VERSIONS = 5;
// Built in order: the bundle embeds the rasterizer's and the translations' hashed names
// and SRI hashes, the loader the bundle's.
const ENTRIES = {
  // Only ever loaded by the bundle, by its hashed name: no stable name.
  // It bundles modern-screenshot (MIT), whose licence travels inside the file.
  raster: { src: "src/raster-entry.ts", stem: "heed-raster", stable: false, define: () => ({}), banner: licence("modern-screenshot") },
  // The built-in translations, fetched only for visitors whose language is not English.
  locales: { src: "src/locales-entry.ts", stem: "heed-locales", stable: false, define: () => ({}) },
  // The opt-in console/network recorder, fetched only by pages that enable capture (heed#7).
  recorder: { src: "src/recorder-entry.ts", stem: "heed-recorder", stable: false, define: () => ({}) },
  widget: {
    src: "src/index.ts",
    stem: "heed-widget",
    define: (m) => ({
      __HEED_VERSION__: JSON.stringify(pkg.version),
      __HEED_RASTER__: JSON.stringify(m.entries.raster),
      __HEED_RASTER_INTEGRITY__: JSON.stringify(m.files[m.entries.raster].integrity),
      __HEED_LOCALES__: JSON.stringify(m.entries.locales),
      __HEED_LOCALES_INTEGRITY__: JSON.stringify(m.files[m.entries.locales].integrity),
      __HEED_RECORDER__: JSON.stringify(m.entries.recorder),
      __HEED_RECORDER_INTEGRITY__: JSON.stringify(m.files[m.entries.recorder].integrity),
    }),
  },
  loader: {
    src: "src/loader.ts",
    stem: "heed",
    define: (m) => ({
      __HEED_BUNDLE__: JSON.stringify(m.entries.widget),
      __HEED_INTEGRITY__: JSON.stringify(m.files[m.entries.widget].integrity),
      __HEED_RECORDER__: JSON.stringify(m.entries.recorder),
      __HEED_RECORDER_INTEGRITY__: JSON.stringify(m.files[m.entries.recorder].integrity),
    }),
  },
};

const sha = (algo, data, enc) => createHash(algo).update(data).digest(enc);

async function bundle(src, define, banner) {
  const result = await build({
    define,
    banner: banner ? { js: banner } : undefined,
    entryPoints: [join(here, src)],
    bundle: true,
    format: "iife",
    minify: true,
    target: ["es2020"],
    legalComments: "none",
    write: false,
    charset: "utf8",
  });
  return result.outputFiles[0].contents;
}

const MANIFEST = join(OUT_DIR, "manifest.json");
const manifest = { version: pkg.version, entries: {}, files: {} };
const outputs = {};
let overBudget = false;
for (const [entry, { src, stem, define, stable = true, banner }] of Object.entries(ENTRIES)) {
  const code = await bundle(src, define(manifest), banner);
  const name = `${stem}-${sha("sha256", code, "hex").slice(0, 10)}.js`;
  const gz = gzipSync(code, { level: 9 }).length;
  outputs[name] = code;
  manifest.entries[entry] = name;
  manifest.files[name] = {
    integrity: `sha384-${sha("sha384", code, "base64")}`,
    immutable: true,
    version: pkg.version,
    bytes: code.length,
    gzip_bytes: gz,
  };
  if (stable) {
    outputs[`${stem}.js`] = code;
    // No integrity for the stable name: its content changes with every upgrade.
    manifest.files[`${stem}.js`] = { immutable: false, bytes: code.length, gzip_bytes: gz };
  }
  console.log(`${name}: ${code.length} B, ${gz} B gzipped (budget ${BUDGETS[entry]})`);
  if (gz > BUDGETS[entry]) overBudget = true;
}
if (overBudget) {
  console.error("widget build: over the gzip budget (build.mjs BUDGETS)");
  process.exit(1);
}

Object.assign(manifest.files, retainedFiles());

mkdirSync(OUT_DIR, { recursive: true });
for (const old of readdirSync(OUT_DIR)) {
  if (old.endsWith(".js") && !(old in manifest.files)) rmSync(join(OUT_DIR, old));
}
for (const [name, code] of Object.entries(outputs)) writeFileSync(join(OUT_DIR, name), code);
writeFileSync(join(OUT_DIR, "manifest.json"), JSON.stringify(manifest, null, 2) + "\n");


/** Hashed files of earlier versions, from the manifest being replaced. */
function retainedFiles() {
  if (!existsSync(MANIFEST)) return {};
  const previous = JSON.parse(readFileSync(MANIFEST, "utf8"));
  const older = Object.entries(previous.files)
    .filter(([, meta]) => meta.immutable)
    .map(([name, meta]) => [name, { ...meta, version: meta.version ?? previous.version }])
    .filter(([name, meta]) => meta.version !== pkg.version && existsSync(join(OUT_DIR, name)));
  const keep = [...new Set(older.map(([, m]) => m.version))].sort(compareVersions).slice(-RETAIN_VERSIONS);
  return Object.fromEntries(older.filter(([, m]) => keep.includes(m.version)));
}

function compareVersions(a, b) {
  const pa = a.split(".").map(Number);
  const pb = b.split(".").map(Number);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const d = (pa[i] ?? 0) - (pb[i] ?? 0);
    if (d) return d;
  }
  return 0;
}

