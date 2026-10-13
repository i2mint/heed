// The licence comment of a bundled dependency, for the files that include it.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));

export function licence(pkgName) {
  const text = readFileSync(join(here, "node_modules", pkgName, "LICENSE"), "utf8").trim();
  return `/*! Includes ${pkgName}:\n${text.replaceAll("*/", "* /")}\n*/`;
}
