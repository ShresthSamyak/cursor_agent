// Source-side masking for questions typed into the overlay (PDF p. 16, "redaction at the source").
// The core redacts too; this is defence in depth so a pasted card number or key never leaves
// the overlay at all. Pure functions, no DOM.

const KEY_PATTERNS = [
  /\bsk-[A-Za-z0-9_-]{16,}\b/g,                 // OpenAI / Anthropic style secret keys
  /\bAKIA[0-9A-Z]{16}\b/g,                      // AWS access key id
  /\bgh[pousr]_[A-Za-z0-9]{30,}\b/g,            // GitHub tokens
  /\bxox[abprs]-[A-Za-z0-9-]{10,}\b/g,          // Slack tokens
  /\bAIza[0-9A-Za-z_-]{35}\b/g,                 // Google API keys
  /\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b/g, // JWTs
];

// One-time codes only when the text says it is one, so "₹4500" or "flight 6400" survive.
const OTP_PATTERN = /\b(otp|one[- ]time (?:code|password)|verification code|2fa code|passcode)\b[^0-9]{0,20}(\d{4,8})\b/gi;

export function luhnValid(digits) {
  let sum = 0;
  let double = false;
  for (let i = digits.length - 1; i >= 0; i--) {
    let d = digits.charCodeAt(i) - 48;
    if (double) { d *= 2; if (d > 9) d -= 9; }
    sum += d;
    double = !double;
  }
  return digits.length >= 13 && sum % 10 === 0;
}

/** Returns {text, masked: string[]} where masked names what was hidden. */
export function redact(input) {
  let text = String(input ?? "");
  const masked = new Set();

  text = text.replace(/\b(?:\d[ -]?){12,18}\d\b/g, m => {
    const digits = m.replace(/\D/g, "");
    if (digits.length >= 13 && digits.length <= 19 && luhnValid(digits)) {
      masked.add("card number");
      return "[card number]";
    }
    return m;
  });

  for (const re of KEY_PATTERNS) {
    text = text.replace(re, () => { masked.add("secret key"); return "[secret]"; });
  }

  text = text.replace(OTP_PATTERN, (m, label, code) => {
    masked.add("one-time code");
    return m.replace(code, "[code]");
  });

  return { text, masked: [...masked] };
}
