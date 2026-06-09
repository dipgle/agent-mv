/**
 * Soft quota tracking for web chat providers.
 *
 * Limit: 10 calls/hour per provider (configurable via WCR_QUOTA_PER_HOUR).
 * State persisted in eval/web_chat_quota.json (relative to PROJECT_LOG_DIR).
 *
 * This is a soft limit — it protects against accidentally hammering a provider
 * in a pipeline loop, not a hard security control.
 */

import { readFileSync, writeFileSync, mkdirSync, existsSync } from "fs";
import { join, dirname } from "path";

const QUOTA_PER_HOUR = parseInt(process.env.WCR_QUOTA_PER_HOUR ?? "10", 10);

// Resolve quota file path: prefer sibling to devlog, fall back to process CWD.
const LOG_DIR = process.env.PROJECT_LOG_DIR ?? "";
// Walk up from LOG_DIR to find eval/ sibling; otherwise use a temp path.
const EVAL_DIR = LOG_DIR
  ? join(dirname(LOG_DIR), "eval")
  : join(process.cwd(), "eval");
const QUOTA_FILE = join(EVAL_DIR, "web_chat_quota.json");

type ProviderName = string;

interface QuotaEntry {
  /** ISO timestamp of the start of the current 1-hour window */
  windowStart: string;
  /** Call count in the current window */
  count: number;
}

type QuotaState = Record<ProviderName, QuotaEntry>;

function loadState(): QuotaState {
  if (!existsSync(QUOTA_FILE)) return {};
  try {
    const raw = readFileSync(QUOTA_FILE, "utf-8");
    return JSON.parse(raw) as QuotaState;
  } catch {
    return {};
  }
}

function saveState(state: QuotaState): void {
  try {
    mkdirSync(EVAL_DIR, { recursive: true });
    writeFileSync(QUOTA_FILE, JSON.stringify(state, null, 2), "utf-8");
  } catch {
    // Best-effort — quota file failure should not block the call
  }
}

function hourWindowStart(): string {
  const now = new Date();
  now.setMinutes(0, 0, 0);
  return now.toISOString();
}

/**
 * Returns true if the provider is within quota, false if the soft limit is hit.
 */
export async function checkQuota(provider: ProviderName): Promise<boolean> {
  const state = loadState();
  const entry = state[provider];
  const currentWindow = hourWindowStart();

  if (!entry || entry.windowStart !== currentWindow) {
    // New window — always allow
    return true;
  }

  return entry.count < QUOTA_PER_HOUR;
}

/**
 * Record one call for the provider (call AFTER a successful dispatch).
 */
export async function recordCall(provider: ProviderName): Promise<void> {
  const state = loadState();
  const currentWindow = hourWindowStart();
  const entry = state[provider];

  if (!entry || entry.windowStart !== currentWindow) {
    state[provider] = { windowStart: currentWindow, count: 1 };
  } else {
    state[provider] = { ...entry, count: entry.count + 1 };
  }

  saveState(state);
}

/**
 * Return current quota state for all providers (for diagnostics).
 */
export function getQuotaStatus(): Record<ProviderName, { remaining: number; windowStart: string }> {
  const state = loadState();
  const currentWindow = hourWindowStart();
  const result: ReturnType<typeof getQuotaStatus> = {};

  for (const provider of ["perplexity", "lmarena", "huggingchat"]) {
    const entry = state[provider];
    if (!entry || entry.windowStart !== currentWindow) {
      result[provider] = { remaining: QUOTA_PER_HOUR, windowStart: currentWindow };
    } else {
      result[provider] = {
        remaining: Math.max(0, QUOTA_PER_HOUR - entry.count),
        windowStart: entry.windowStart,
      };
    }
  }

  return result;
}
