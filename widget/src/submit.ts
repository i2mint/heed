/**
 * Turn a filled-in form into the multipart POST heed's ingest route expects, send it,
 * and translate every answer the hardened route can give (heed#5: origin allow-list,
 * payload caps, honeypot, bot gate, rate limits, moderation) into an outcome the
 * dialog can show. No DOM here, so it is tested without a browser.
 */
import { CONTRACT } from "./contract.gen";
import type { EnvironmentPayload } from "./env";
import type { Category, HeedOptions } from "./options";

const F = CONTRACT.fields;
const L = CONTRACT.limits;

export interface ReportDraft {
  category: Category;
  title: string;
  body: string;
}

export interface SubmitInput {
  draft: ReportDraft;
  env: EnvironmentPayload;
  pageUrl: string;
  /** What the hidden honeypot field holds (empty for a human). */
  honeypot: string;
  screenshot?: Blob | null;
}

export type ErrorKind =
  | "invalid"
  | "too_large"
  | "forbidden"
  | "bot"
  | "rate_limited"
  | "unavailable"
  | "network"
  | "timeout";

export type SubmitOutcome =
  | { ok: true; id: string; status: "received" | "held"; url: string | null }
  | { ok: false; kind: ErrorKind; retryAfterSeconds: number | null };

/** Everything a visitor can be told, in one place (the seam for translations). */
export const MESSAGES = {
  sending: "Sending…",
  received: "Thank you! Your report was sent.",
  held: "Thank you! Your report was received and will be reviewed before it is published.",
  view: "View your report",
  titleRequired: "Please give your report a short title.",
  noEndpoint: "This feedback form is not set up yet (no endpoint).",
  invalid: "The report could not be accepted. Please shorten or simplify it and try again.",
  too_large: "The report is too large to send. Please shorten it.",
  forbidden: "This site is not allowed to send reports to this feedback service.",
  bot: "We could not confirm you are human. Please reload the page and try again.",
  rate_limited: "Too many reports were sent recently. Please try again later.",
  unavailable: "The feedback service is unavailable right now. Please try again shortly.",
  network: "Could not reach the feedback service. Check your connection and try again.",
  timeout: "The feedback service took too long to answer. Please try again.",
  retryIn: (s: number) => `You can try again in ${s} seconds.`,
} as const;

export function buildFormData(input: SubmitInput, options: HeedOptions, botToken: string | null): FormData {
  const form = new FormData();
  form.set(F.title, input.draft.title.trim().slice(0, L.title_chars));
  form.set(F.body, input.draft.body.slice(0, L.body_chars));
  form.set(F.category, input.draft.category);
  form.set(F.page_url, input.pageUrl);
  form.set(F.env, JSON.stringify(input.env));
  if (options.anonId) form.set(F.anon_id, options.anonId.slice(0, L.anon_id_chars));
  if (options.honeypotField) form.set(options.honeypotField, input.honeypot);
  if (botToken) form.set(options.botTokenField, botToken);
  if (input.screenshot) form.set(F.screenshot, input.screenshot, screenshotName(input.screenshot.type));
  return form;
}

function screenshotName(type: string): string {
  const ext = { "image/jpeg": "jpg", "image/webp": "webp" }[type] ?? "png";
  return `screenshot.${ext}`;
}

/**
 * POST the report. Never throws. `options.timeoutMs` bounds the whole attempt:
 * getting the bot token, the request, and reading the answer.
 * `fetchImpl` is injectable for tests.
 */
export async function sendReport(
  input: SubmitInput,
  options: HeedOptions,
  fetchImpl: typeof fetch = fetch,
): Promise<SubmitOutcome> {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const deadline = new Promise<SubmitOutcome>((resolve) => {
    timer = setTimeout(() => {
      controller.abort();
      resolve(failure("timeout"));
    }, options.timeoutMs);
  });
  try {
    return await Promise.race([attempt(input, options, fetchImpl, controller.signal), deadline]);
  } finally {
    clearTimeout(timer);
  }
}

const failure = (kind: ErrorKind, retryAfterSeconds: number | null = null): SubmitOutcome => ({ ok: false, kind, retryAfterSeconds });

async function attempt(input: SubmitInput, options: HeedOptions, fetchImpl: typeof fetch, signal: AbortSignal): Promise<SubmitOutcome> {
  let botToken: string | null = null;
  if (options.botToken) {
    try {
      botToken = (await options.botToken()) || null;
    } catch (e) {
      console.warn("heed: the bot-token provider failed", e);
      return failure("bot");
    }
  }
  if (signal.aborted) return failure("timeout");
  let response: Response;
  try {
    response = await fetchImpl(options.endpoint, {
      method: "POST",
      body: buildFormData(input, options, botToken),
      mode: "cors",
      credentials: options.withCredentials ? "include" : "omit",
      signal,
    });
  } catch {
    if (signal.aborted) return failure("timeout");
    // A backend that refuses the page's origin answers without CORS headers, which the
    // browser reports exactly like a network failure: tell the site owner how to check.
    console.warn(
      `heed: could not reach ${options.endpoint}. If the backend is up, check that this page's origin ` +
        `(${globalThis.location?.origin}) is in its allowed_origins.`,
    );
    return failure("network");
  }
  return readOutcome(response);
}

async function readOutcome(response: Response): Promise<SubmitOutcome> {
  const body = await response.json().catch(() => null);
  if (response.ok) {
    if (!body || body.ok === false || typeof body.id !== "string") {
      return failure("unavailable");
    }
    return {
      ok: true,
      id: body.id,
      status: body.status === CONTRACT.statuses.held ? "held" : "received",
      url: safeLink(body.url),
    };
  }
  return failure(errorKind(response.status, body?.detail), retryAfter(response));
}

function errorKind(status: number, detail: unknown): ErrorKind {
  if (status === 403) return detail === "bot verification failed" ? "bot" : "forbidden";
  if (status === 413) return "too_large";
  if (status === 429) return "rate_limited";
  if (status >= 400 && status < 500) return "invalid";
  return "unavailable";
}

function retryAfter(response: Response): number | null {
  const n = Number(response.headers.get("Retry-After"));
  return Number.isFinite(n) && n > 0 ? Math.ceil(n) : null;
}

/** Only an http(s) link the server returned is ever shown (never `javascript:`). */
export function safeLink(url: unknown): string | null {
  if (typeof url !== "string") return null;
  try {
    const u = new URL(url);
    return u.protocol === "https:" || u.protocol === "http:" ? u.href : null;
  } catch {
    return null;
  }
}
