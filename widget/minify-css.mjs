// An esbuild plugin that minifies the widget's stylesheet (src/styles.ts's `CSS`
// template literal) at build time: esbuild minifies JS, but leaves the text of a
// template literal untouched, and the stylesheet is the widget's largest string.
import { readFile } from "node:fs/promises";
import { transform } from "esbuild";

const CSS_LITERAL = /(export const CSS = `)([^`]*)(`)/;

export const minifyCss = {
  name: "heed-minify-css",
  setup(build) {
    build.onLoad({ filter: /[\\/]src[\\/]styles\.ts$/ }, async ({ path }) => {
      const source = await readFile(path, "utf8");
      const match = source.match(CSS_LITERAL);
      if (!match || match[2].includes("${")) throw new Error(`heed-minify-css: no plain CSS literal in ${path}`);
      const { code } = await transform(match[2], { loader: "css", minify: true });
      return { contents: source.replace(CSS_LITERAL, (_, a, __, c) => a + code.trim() + c), loader: "ts" };
    });
  },
};
