/**
 * Entry point of `heed-raster-<hash>.js`: registers the rasterizer for the widget
 * bundle that loaded this file (capture.ts, `loadRaster`).
 */
import { type Raster, capture } from "./raster.js";

(window as unknown as { __heedRaster?: Raster }).__heedRaster = { capture };
