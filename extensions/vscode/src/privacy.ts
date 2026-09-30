/**
 * Source-side privacy filters (PDF p. 15-16, docs/bridge-protocol.md rules).
 *
 * - hover/select targets that look like secrets are sent with sensitive=true,
 *   so the core drops them before they reach the trail;
 * - terminal output has secrets and card numbers masked before it leaves;
 * - document text is sent as-is (the code mentor's secret check needs it), but
 *   files matching trail.excludeFiles are never read at all.
 */

// Superset of trail/core/privacy.py _SECRET, plus other common key formats.
const SECRET_SOURCES = [
  String.raw`\bsk-[A-Za-z0-9_-]{16,}`, // OpenAI-style / generic "sk-" keys
  String.raw`\bAKIA[A-Z0-9]{16}\b`, // AWS access key id
  String.raw`\bgh[pousr]_[A-Za-z0-9]{20,}\b`, // GitHub tokens
  String.raw`\bgithub_pat_[A-Za-z0-9_]{20,}`,
  String.raw`\bxox[abprs]-[A-Za-z0-9-]{10,}`, // Slack
  String.raw`\bAIza[0-9A-Za-z_-]{35}\b`, // Google API key
  String.raw`\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{16,}`, // Stripe
  String.raw`-----BEGIN [A-Z ]*PRIVATE KEY-----`,
  String.raw`\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}`, // JWT
];
const SECRET_RE = new RegExp(SECRET_SOURCES.join("|"));
const SECRET_RE_G = new RegExp(SECRET_SOURCES.join("|"), "g");

// `api_key = "..."`, `password: '...'`: an assignment of a long literal to a secret-ish name.
const ASSIGNED_SECRET_RE =
  /\b[\w-]*(?:api[_-]?key|secret|token|passw(?:or)?d|private[_-]?key)[\w-]*\s*[:=]\s*["'][^"'\s]{12,}["']/i;

const CARD_RE = /(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)/g;

function isLuhn(value: string): boolean {
  const digits = value.replace(/\D/g, "");
  if (digits.length < 13 || digits.length > 19) return false;
  let total = 0;
  for (let i = 0; i < digits.length; i++) {
    let d = Number(digits[digits.length - 1 - i]);
    if (i % 2) {
      d *= 2;
      if (d > 9) d -= 9;
    }
    total += d;
  }
  return total % 10 === 0;
}

/** True if the text contains something that should never enter the trail. */
export function looksSensitive(text: string): boolean {
  if (SECRET_RE.test(text) || ASSIGNED_SECRET_RE.test(text)) return true;
  for (const m of text.matchAll(CARD_RE)) if (isLuhn(m[0])) return true;
  return false;
}

/** True if a pasted/inserted chunk contains a key pattern (the "interrupt instantly" case). */
export function containsSecret(text: string): boolean {
  return SECRET_RE.test(text) || ASSIGNED_SECRET_RE.test(text);
}

/** Mask secrets and Luhn-valid card numbers. */
export function redact(text: string): string {
  return text
    .replace(SECRET_RE_G, "[redacted secret]")
    .replace(CARD_RE, (m) => (isLuhn(m) ? "[redacted card]" : m));
}

// CSI sequences, OSC sequences (incl. VS Code shell-integration OSC 633), other escapes.
const ANSI_RE = /\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]/g;

/** Strip terminal escape codes and normalise line endings. */
export function cleanTerminal(text: string): string {
  return text
    .replace(ANSI_RE, "")
    .replace(/\r\n/g, "\n")
    .replace(/[^\n]*\r(?!\n)/g, "") // progress-bar overwrites: keep the last rewrite
    .replace(/[\x00-\x08\x0b-\x1f\x7f]/g, "");
}
