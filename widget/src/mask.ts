/**
 * Masking of personal data and secrets in captured console messages and request URLs,
 * applied as an entry is recorded: nothing unmasked is ever kept in memory, let alone
 * sent (redact *before* capture, heed#7).
 *
 * Mirrors heed's `redact.py`, which applies the same rules again on the server. Keep
 * the two in step. The rules are greedy on purpose: a lost hex string costs a little
 * debugging, a leaked token costs an incident.
 */
export const REDACTED = "[redacted]";

// Sensitive keys in "key=value" / "key: value" / "key": "value". The long words match
// inside a longer key (client_secret, AWS_SECRET_ACCESS_KEY); the short ones only alone.
const LONG_KEYS =
  "secret|token|passw|pwd|api[_-]?key|apikey|auth|session|cookie|csrf|xsrf" +
  "|credential|private[_-]?key|card[_-]?number|credit[_-]?card";
/** Is `key` one whose value is a secret (`password`, `client_secret`, `x-auth-token`…)? */
export const isSensitiveKey = (key: string): boolean => SENSITIVE_KEY.test(key);
const SENSITIVE_KEY = new RegExp(`^(?:[A-Za-z0-9_-]*(?:${LONG_KEYS})[A-Za-z0-9_-]*|pass|pin|sid|otp|ssn|cvv|cvc|sig|signature)$`, "i");
const W = "A-Za-z0-9_-";
const HEX = "0-9A-Fa-f";

type Replacement = string | ((match: string, ...groups: string[]) => string);

/** Replace only a run holding both a letter and a digit (a key, not a word). */
const ifMixed = (replacement: string) => (run: string) => (/\d/.test(run) && /[A-Za-z]/.test(run) ? replacement : run);

/** When the key is sensitive, replace the value, keeping its quotes. */
const secretValue = (match: string, key: string, sep: string, value: string) => {
  if (!SENSITIVE_KEY.test(key)) return match;
  const quote = value[0] === '"' || value[0] === "'" ? value[0] : "";
  return `${key}${sep}${quote}${REDACTED}${quote}`;
};

/**
 * Applied in order: the specific shapes (an Authorization header, a JWT, a provider key)
 * before the generic ones. Every rule starts at the beginning of a run, so none rescans
 * a run from each of its positions: matching stays linear on hostile text.
 */
const RULES: [RegExp, Replacement][] = [
  [/(\b(?:proxy-)?authorization["']?\s*[:=]\s*)(["']?)[^\r\n"']*/gi, `$1$2${REDACTED}`],
  [new RegExp(`\\beyJ[${W}]{5,}\\.[${W}]{5,}\\.[${W}]*`, "g"), "[jwt]"],
  [
    new RegExp(
      "\\b(?:(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{8,}|gh[pousr]_[A-Za-z0-9]{8,}|github_pat_[A-Za-z0-9_]{20,}" +
        `|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|AIza[${W}]{30,}|sk-[${W}]{16,})`,
      "g",
    ),
    "[key]",
  ],
  [/\b(bearer|basic|digest|token)(\s+)(?!\[)[A-Za-z0-9._~+/=-]{3,}/gi, `$1$2${REDACTED}`],
  [new RegExp(`(?<![${W}])([${W}]+)(["']?\\s*[:=]\\s*)("[^"\\n]*"|'[^'\\n]*'|[^\\s"'&,;}{\\[]+)`, "g"), secretValue as Replacement],
  [/(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g, "[email]"],
  [/(?<![A-Za-z0-9/+=])[A-Za-z0-9/+]{40}(?![A-Za-z0-9/+=])/g, ifMixed("[key]")],
  [new RegExp(`(?<![${W}])[${W}]{32,}`, "g"), ifMixed("[id]")],
  [
    new RegExp(`(?<![${HEX}:])(?:(?:[${HEX}]{1,4}:){7}[${HEX}]{1,4}|(?:[${HEX}]{1,4}:){1,6}(?::[${HEX}]{1,4}){1,6})(?![${HEX}:])`, "g"),
    "[ip]",
  ],
  [/(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])/g, "[ip]"],
  [/(?<![\w-])\d{3}-\d{2}-\d{4}(?![\w-])/g, "[ssn]"],
  [/(?<![\w(])\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\w)/g, "[phone]"],
  [/\+\d[\d ().-]{6,}\d/g, "[phone]"],
  [/(\/home\/|\/Users\/|\b[A-Za-z]:\\Users\\)[^/\\\s"']+/g, "$1[user]"],
];

/** URLs inside text are masked like request URLs (`maskUrl`), before the other rules. */
const URL_IN_TEXT = /\bhttps?:\/\/[^\s"'<>`]+/g;
const CARD_CANDIDATE = /\b\d(?:[ -]?\d){12,18}\b/g;

function luhnOk(digits: string): boolean {
  let total = 0;
  for (let i = 0; i < digits.length; i++) {
    let n = Number(digits[digits.length - 1 - i]);
    if (i % 2) n = n > 4 ? n * 2 - 9 : n * 2;
    total += n;
  }
  return total % 10 === 0;
}

/** Base that only lets a relative URL parse; never emitted. */
const PARSE_HOST = "heed.invalid";
const PARSE_BASE = `http://${PARSE_HOST}`;

/** `text` with personal data and secrets replaced by placeholders. */
export function maskText(text: string): string {
  text = text.replace(URL_IN_TEXT, (url) => maskUrl(url));
  for (const [pattern, replacement] of RULES) text = text.replace(pattern, replacement as string);
  return text.replace(CARD_CANDIDATE, (m) => (luhnOk(m.replace(/\D/g, "")) ? "[card]" : m));
}

// \p{Nd}: Python's \d is Unicode-aware, so non-ASCII digits are ids here too.
const ID_SEGMENT = /^(?:\p{Nd}+|(?=[A-Za-z_.~+=-]*\p{Nd})[A-Za-z0-9_.~+=-]{12,})$/u;
const MIN_LETTER_TOKEN_LEN = 16; // all-letter tokens shorter than this read as words
const MIN_TOKEN_ENTROPY = 3.8; // bits per character; slugs and words fall below
const ALNUM = /^[\p{L}\p{N}]+$/u;

/** Percent-decoding that leaves a malformed escape as it is (Python's `unquote`). */
const lenientDecode = (s: string): string =>
  s.replace(/(?:%[0-9A-Fa-f]{2})+/g, (run) => {
    try {
      return decodeURIComponent(run);
    } catch {
      return run;
    }
  });

function entropy(text: string): number {
  const counts = new Map<string, number>();
  const chars = [...text];
  for (const c of chars) counts.set(c, (counts.get(c) ?? 0) + 1);
  let bits = 0;
  for (const n of counts.values()) bits -= (n / chars.length) * Math.log2(n / chars.length);
  return bits;
}

/** Is `segment` (percent-decoded) an id or an opaque token? Mirrors `_looks_like_token` in `redact.py`. */
function looksLikeToken(segment: string): boolean {
  if (ID_SEGMENT.test(segment)) return true;
  const dots = segment.split(".").length - 1;
  const body = dots > 1 ? segment.replaceAll(".", "") : segment; // slugs and file names are words
  return [...segment].length >= MIN_LETTER_TOKEN_LEN && ALNUM.test(body) && entropy(segment) >= MIN_TOKEN_ENTROPY;
}

/** One path segment: ids and opaque tokens (also behind percent-encoding) and handles go, `;params` too. */
function maskSegment(segment: string): string {
  segment = segment.split(";", 1)[0];
  const decoded = lenientDecode(segment);
  if (looksLikeToken(decoded) || looksLikeToken(decoded.replaceAll("/", ""))) return "[id]";
  if (segment.startsWith("@")) return "[user]";
  return maskText(segment);
}

/**
 * `url` without credentials, fragment, query values (the keys are kept), id-like path
 * segments or handles, resolved against `base` when relative (kept relative when there is no `base`;
 * WHATWG parsing normalises dot segments and backslashes, which `urlsplit` does not: no leak). A `data:` or `blob:` URL keeps only its scheme;
 * anything that does not parse is masked as text.
 */
export function maskUrl(url: string, base?: string): string {
  let u: URL;
  let relative = false;
  try {
    u = new URL(url, base);
  } catch {
    try {
      u = new URL(url, PARSE_BASE); // relative and no base: mask it in its relative form, like `redact_url`
      relative = true;
    } catch {
      return maskText(url);
    }
  }
  if (u.protocol !== "http:" && u.protocol !== "https:") return `${u.protocol}${REDACTED}`; // data:, blob: carry content
  let path = u.pathname.split("/").map(maskSegment).join("/");
  const keys = [...u.searchParams.keys()].map((k) => `${encodeURIComponent(maskText(k))}=${REDACTED}`);
  const query = keys.length ? `?${keys.join("&")}` : "";
  if (!relative) return `${u.protocol}//${u.host}${path}${query}`;
  if (u.host !== PARSE_HOST) return `//${u.host}${path}${query}`; // protocol-relative
  if (path.startsWith("//")) path = `/.${path}`; // "/a/..//b" must not read as a host
  else if (!/^[/\\]/.test(url.trimStart())) path = path.slice(1); // "a/b", "?q=1": no leading slash was given
  return `${path}${query}`;
}
