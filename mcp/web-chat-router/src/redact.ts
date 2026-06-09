/**
 * Privacy guard — must short-circuit before any browser interaction.
 *
 * Blocks prompts that contain:
 *  - API key patterns (sk-*, AKIA*, AIza*)
 *  - Absolute paths (/Users/, C:\Users\)
 *  - Client brand identifiers (client_name, brand/, assets/proprietary/)
 *
 * Cannot be disabled. Checked before every adapter call.
 */

export class BlockedPromptError extends Error {
  readonly matchedPattern: string;
  readonly category: string;

  constructor(pattern: string, category: string) {
    super(
      `Prompt blocked by privacy guard — matched pattern: ${pattern} (category: ${category}). ` +
      `Do not paste proprietary content, API keys, or client assets into web chat adapters.`
    );
    this.name = "BlockedPromptError";
    this.matchedPattern = pattern;
    this.category = category;
  }
}

interface RedactRule {
  /** Human-readable category label shown in the error. */
  category: string;
  /** Pattern tested against the prompt. */
  pattern: RegExp | string;
}

// Rules checked in order. First match wins and raises BlockedPromptError.
const REDACT_RULES: RedactRule[] = [
  // --- API key patterns -------------------------------------------------------
  { category: "api-key", pattern: /\bsk-[A-Za-z0-9_-]{10,}/i },
  { category: "api-key", pattern: /\bAKIA[0-9A-Z]{16}\b/ },
  { category: "api-key", pattern: /\bAIza[0-9A-Za-z_-]{35}\b/ },
  // Generic Bearer / Authorization header fragments
  { category: "api-key", pattern: /\bBearer\s+[A-Za-z0-9._-]{20,}/i },

  // --- Absolute paths ---------------------------------------------------------
  { category: "absolute-path", pattern: /\/Users\/[A-Za-z0-9_.-]+\// },
  { category: "absolute-path", pattern: /C:\\Users\\/i },
  { category: "absolute-path", pattern: /\/home\/[A-Za-z0-9_.-]+\// },

  // --- Client brand / proprietary asset identifiers --------------------------
  // These match substring; intentionally case-insensitive per privacy convention.
  { category: "client-brand", pattern: /\bclient[_\s-]?name\b/i },
  { category: "client-brand", pattern: /\bbrand\//i },
  { category: "client-brand", pattern: /\bassets\/proprietary\//i },
];

/**
 * Validate prompt before sending to any external web chat adapter.
 *
 * @throws {BlockedPromptError} if any pattern matches.
 */
export function assertSafePrompt(prompt: string): void {
  for (const rule of REDACT_RULES) {
    const matched =
      rule.pattern instanceof RegExp
        ? rule.pattern.test(prompt)
        : prompt.toLowerCase().includes(rule.pattern.toLowerCase());

    if (matched) {
      const patternStr =
        rule.pattern instanceof RegExp
          ? rule.pattern.source
          : rule.pattern;
      throw new BlockedPromptError(patternStr, rule.category);
    }
  }
}
