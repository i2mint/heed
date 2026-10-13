// Build the widget into heed/static/widget/, the folder the Python package ships and
// heed.widget.make_widget_router serves. Each entry is written twice: under a
// content-hashed name, cached forever and meant for pinning with SRI, and under a
// stable name (`<stem>.js`, short cache) that keeps working across heed upgrades.
// manifest.json lists every file with its SRI hash and size.
// Fails when a file outgrows its gzip budget: the widget loads on other people's sites.
import { build } from "esbuild";
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

const here = dirname(fileURLToPath(import.meta.url));
const OUT_DIR = join(here, "..", "heed", "static", "widget");
const pkg = JSON.parse(readFileSync(join(here, "package.json"), "utf8"));
// Gzipped-size budgets in bytes, per entry.
const BUDGETS = { widget: 8_000 };
const ENTRIES = { widget: { src: "src/index.ts", stem: "heed-widget" } };

const sha = (algo, data, enc) => createHash(algo).update(data).digest(enc);

async function bundle(src) {
  const result = await build({
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

const manifest = { version: pkg.version, entries: {}, files: {} };
const outputs = {};
let overBudget = false;
for (const [entry, { src, stem }] of Object.entries(ENTRIES)) {
  const code = await bundle(src);
  const name = `${stem}-${sha("sha256", code, "hex").slice(0, 10)}.js`;
  const gz = gzipSync(code, { level: 9 }).length;
  outputs[name] = code;
  manifest.entries[entry] = name;
  manifest.files[name] = {
    integrity: `sha384-${sha("sha384", code, "base64")}`,
    immutable: true,
    bytes: code.length,
    gzip_bytes: gz,
  };
  outputs[`${stem}.js`] = code;
  // No integrity for the stable name: its content changes with every upgrade.
  manifest.files[`${stem}.js`] = { immutable: false, bytes: code.length, gzip_bytes: gz };
  console.log(`${name}: ${code.length} B, ${gz} B gzipped (budget ${BUDGETS[entry]})`);
  if (gz > BUDGETS[entry]) overBudget = true;
}
if (overBudget) {
  console.error("widget build: over the gzip budget (build.mjs BUDGETS)");
  process.exit(1);
}

mkdirSync(OUT_DIR, { recursive: true });
for (const old of readdirSync(OUT_DIR)) {
  if (old.endsWith(".js") || old === "manifest.json") rmSync(join(OUT_DIR, old));
}
for (const [name, code] of Object.entries(outputs)) writeFileSync(join(OUT_DIR, name), code);
writeFileSync(join(OUT_DIR, "manifest.json"), JSON.stringify(manifest, null, 2) + "\n");
