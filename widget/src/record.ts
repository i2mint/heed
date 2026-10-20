/**
 * Opt-in console/network capture from the widget bundle (heed#7): load the recorder
 * once per page when an element enables it, and hand a report what it recorded.
 *
 * The recorder is a file next to the bundle (sibling.ts), fetched only by pages whose
 * embedder turned capture on; apps that bundle heed import it lazily instead
 * (`setRecorderLoader`, lib.ts). One recorder serves every element on the page.
 */
import type { RecordKinds, Recorded, Recorder, startRecorder } from "./recorder.js";
import { loadSibling } from "./sibling.js";

declare const __HEED_RECORDER__: string;
declare const __HEED_RECORDER_INTEGRITY__: string;

type RecorderModule = { startRecorder: typeof startRecorder; early?: Recorder; ignored?: Set<string> };
type HeedGlobals = Window & { __heedRecorder?: RecorderModule };

let loadRecorder = (win: Window): Promise<RecorderModule> =>
  loadSibling(win, __HEED_RECORDER__, __HEED_RECORDER_INTEGRITY__, () => (win as HeedGlobals).__heedRecorder, "recorder");

/** Replace how the recorder module is obtained (lib.ts does, for apps that bundle heed). */
export function setRecorderLoader(load: (win: Window) => Promise<RecorderModule>): void {
  loadRecorder = load;
}

interface PageRecording {
  recorder: Recorder | null;
  wanted: RecordKinds;
  ignored: Set<string>;
}

const pages = new WeakMap<Window, PageRecording>();

/**
 * Start recording `kinds` on `win` (once per page; more kinds can be added later).
 * Requests to `endpoint` (heed's own report) are left out. Never throws: a recorder
 * that cannot load only means reports carry no capture.
 */
export function ensureRecording(win: Window, kinds: RecordKinds, endpoint: string): void {
  if (!kinds.console && !kinds.network) return;
  let page = pages.get(win);
  if (!page) {
    const fresh: PageRecording = { recorder: null, wanted: { console: false, network: false }, ignored: new Set() };
    page = fresh;
    pages.set(win, fresh);
    loadRecorder(win).then(
      (m) => {
        // A recorder the loader started early (recorder-entry.ts) already runs: adopt it.
        if (m.early && m.ignored) {
          for (const url of fresh.ignored) m.ignored.add(url);
          fresh.ignored = m.ignored;
          fresh.recorder = m.early;
        } else fresh.recorder = m.startRecorder(win, { ignore: (url) => fresh.ignored.has(url) });
        fresh.recorder.enable(fresh.wanted);
      },
      (e) => console.warn("heed: console/network capture is unavailable", e),
    );
  }
  if (endpoint) {
    try {
      page.ignored.add(new URL(endpoint, win.location.href).href.split(/[?#]/)[0]);
    } catch {
      // an endpoint that does not parse is never fetched either
    }
  }
  page.wanted = { console: page.wanted.console || kinds.console, network: page.wanted.network || kinds.network };
  page.recorder?.enable(page.wanted);
}

/** What was recorded on `win` for `kinds`; empty when nothing was (yet). */
export function recorded(win: Window, kinds: RecordKinds): Recorded {
  return pages.get(win)?.recorder?.snapshot(kinds) ?? {};
}

/** Forget the page's recorder, restoring what it wrapped (tests). */
export function stopRecording(win: Window): void {
  pages.get(win)?.recorder?.stop();
  pages.delete(win);
}
