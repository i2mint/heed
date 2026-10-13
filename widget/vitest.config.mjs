import { defineConfig } from "vitest/config";

export default defineConfig({
  define: { __HEED_VERSION__: JSON.stringify("test") },
  test: { environment: "jsdom", include: ["test/**/*.test.ts"], setupFiles: ["test/setup.ts"] },
});
