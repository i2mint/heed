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

const here = dirname(fileURLToPath(import.meta.url));
const OUT_DIR = join(here, "..", "heed", "static", "widget");
const pkg = JSON.parse(readFileSync(join(here, "package.json"), "utf8"));
// Gzipped-size budgets in bytes, per entry.
const BUDGETS = { raster: 14_000, widget: 9_000, loader: 1_300 };
const RETAIN_VERSIONS = 5;
// Built in order: the bundle embeds the rasterizer's hashed name and SRI hash, the
// loader the bundle's.
const ENTRIES = {
  // Only ever loaded by the bundle, by its hashed name: no stable name.
  raster: { src: "src/raster.ts", stem: "heed-raster", stable: false, define: () => ({}) },
  widget: {
    src: "src/index.ts",
    stem: "heed-widget",
    define: (m) => ({
      __HEED_VERSION__: JSON.stringify(pkg.version),
      __HEED_RASTER__: JSON.stringify(m.entries.raster),
      __HEED_RASTER_INTEGRITY__: JSON.stringify(m.files[m.entries.raster].integrity),
    }),
  },
  loader: {
    src: "src/loader.ts",
    stem: "heed",
    define: (m) => ({
      __HEED_BUNDLE__: JSON.stringify(m.entries.widget),
      __HEED_INTEGRITY__: JSON.stringify(m.files[m.entries.widget].integrity),
    }),
  },
};

const sha = (algo, data, enc) => createHash(algo).update(data).digest(enc);

async function bundle(src, define) {
  const result = await build({
    define,
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
for (const [entry, { src, stem, define, stable = true }] of Object.entries(ENTRIES)) {
  const code = await bundle(src, define(manifest));
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
// The rasterizer bundles modern-screenshot (MIT): ship its licence beside it.
const notice = readFileSync(join(here, "node_modules", "modern-screenshot", "LICENSE"), "utf8");
writeFileSync(
  join(OUT_DIR, "THIRD-PARTY-NOTICES.txt"),
  `heed-raster-*.js bundles modern-screenshot (https://github.com/qq15725/modern-screenshot):\n\n${notice}`,
);

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
