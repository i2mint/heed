/**
 * Entry point of `heed-recorder-<hash>.js`: registers the opt-in console/network
 * recorder for the widget bundle that loaded this file (record.ts), and, when the loader
 * tag enables capture, starts it at once (heed#62).
 */
import type { RecordKinds, Recorder } from "./recorder.js";
import { startRecorder } from "./recorder.js";

type Registered = { startRecorder: typeof startRecorder; early?: Recorder; ignored: Set<string> };
const w = window as unknown as { __heedRecorder?: Registered; __heedEarly?: RecordKinds };

// Loaded twice (the loader fetches it early, the bundle may ask too): the first wins.
if (!w.__heedRecorder) {
  const ignored = new Set<string>();
  const registered: Registered = { startRecorder, ignored };
  w.__heedRecorder = registered;
  // The loader asked for capture from the start (heed#62): record from now on, and let the
  // bundle adopt this recorder when it arrives.
  if (w.__heedEarly) {
    registered.early = startRecorder(window, { ignore: (url) => ignored.has(url) });
    registered.early.enable(w.__heedEarly);
  }
}
