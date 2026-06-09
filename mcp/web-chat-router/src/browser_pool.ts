/**
 * Playwright browser pool.
 *
 * Each provider gets its own persistent user-data-dir so cookies / sessions
 * survive across MCP calls within a process lifetime.
 * Browsers are launched lazily on first use and reused thereafter.
 */

import { Browser, BrowserContext, chromium } from "playwright";
import { join } from "path";
import { mkdirSync } from "fs";
import { tmpdir } from "os";

export type ProviderId = "perplexity" | "lmarena" | "huggingchat";

const HEADLESS = process.env.WCR_HEADLESS !== "0"; // default headless

// Base dir for persistent browser profiles (one per provider)
const PROFILE_BASE =
  process.env.WCR_PROFILE_DIR ?? join(tmpdir(), "agent-mv-wcr-profiles");

interface PoolEntry {
  browser: Browser;
  context: BrowserContext;
}

const pool = new Map<ProviderId, PoolEntry>();

function profileDir(provider: ProviderId): string {
  const dir = join(PROFILE_BASE, provider);
  mkdirSync(dir, { recursive: true });
  return dir;
}

/**
 * Return (or lazily create) a Playwright BrowserContext for the given provider.
 * The context uses a persistent profile so sessions / cookies carry over
 * between tool calls within the same process.
 */
export async function getContext(provider: ProviderId): Promise<BrowserContext> {
  const existing = pool.get(provider);
  if (existing) {
    // Verify browser process is still alive
    try {
      existing.browser.contexts(); // throws if browser crashed
      return existing.context;
    } catch {
      pool.delete(provider);
    }
  }

  const userDataDir = profileDir(provider);

  // launchPersistentContext combines browser + context
  const context = await chromium.launchPersistentContext(userDataDir, {
    headless: HEADLESS,
    args: [
      "--no-sandbox",
      "--disable-blink-features=AutomationControlled",
    ],
    // Mimic a real desktop browser to reduce bot-detection risk
    userAgent:
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) " +
      "AppleWebKit/537.36 (KHTML, like Gecko) " +
      "Chrome/124.0.0.0 Safari/537.36",
    viewport: { width: 1280, height: 800 },
    locale: "en-US",
    timezoneId: "America/New_York",
  });

  // We do not have a separate Browser object from launchPersistentContext.
  // Store a sentinel so pool checks know the context is live.
  pool.set(provider, {
    browser: context.browser()!,
    context,
  });

  return context;
}

/**
 * Close all open browser contexts. Called on process exit.
 */
export async function closeAll(): Promise<void> {
  for (const [, entry] of pool) {
    try {
      await entry.context.close();
    } catch {
      // Best-effort cleanup
    }
  }
  pool.clear();
}
