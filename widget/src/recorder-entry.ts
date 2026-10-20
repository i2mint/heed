/**
 * Entry point of `heed-recorder-<hash>.js`: registers the opt-in console/network
 * recorder for the widget bundle that loaded this file (record.ts).
 */
import { startRecorder } from "./recorder.js";

(window as unknown as { __heedRecorder?: { startRecorder: typeof startRecorder } }).__heedRecorder = { startRecorder };
