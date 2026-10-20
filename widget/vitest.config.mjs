import { defineConfig } from "vitest/config";

export default defineConfig({
  define: { __HEED_VERSION__: JSON.stringify("test"), __HEED_RASTER__: JSON.stringify("heed-raster-test.js"), __HEED_RASTER_INTEGRITY__: JSON.stringify(""), __HEED_LOCALES__: JSON.stringify("heed-locales-test.js"), __HEED_LOCALES_INTEGRITY__: JSON.stringify(""), __HEED_RECORDER__: JSON.stringify("heed-recorder-test.js"), __HEED_RECORDER_INTEGRITY__: JSON.stringify("") },
  test: { environment: "jsdom", include: ["test/**/*.test.ts"], setupFiles: ["test/setup.ts"] },
});
