/**
 * Perplexity adapter — no login required.
 *
 * Strategy: navigate to perplexity.ai, submit prompt via the search/ask
 * input, wait for the answer to stream in, then extract the text + citations.
 *
 * Phase 1 uses the web UI (no API key). This means:
 *  - Rate limited by IP (Perplexity's anon tier allows ~5 searches/5 min)
 *  - Response quality mirrors the "Pro Search" toggle state (we don't toggle)
 *  - Layout changes on perplexity.ai will break selectors — update selectors
 *    in SELECTORS block when that happens.
 */

import type { Page } from "playwright";
import { getContext } from "../browser_pool.js";

// DOM selectors — isolated here for easy maintenance when Perplexity updates UI
const SELECTORS = {
  // Main textarea / contenteditable where the query is typed
  input: 'textarea[placeholder*="Ask"]',
  // Fallback contenteditable if textarea not found
  inputFallback: '[contenteditable="true"]',
  // Submit button (keyboard Enter also works)
  submit: 'button[aria-label*="Submit"], button[data-testid*="send"]',
  // Answer container rendered after streaming completes
  answerBlock: '[data-testid="answer"], .prose, [class*="answer"]',
  // Individual citation links shown below the answer
  citations: 'a[href^="http"][class*="citation"], [class*="source"] a[href^="http"]',
};

const TIMEOUT_MS = 60_000; // 1 minute max for streaming to complete
const POLL_INTERVAL_MS = 1_000;
const STABLE_SETTLE_MS = 3_000; // wait this long with no DOM change before declaring done

export interface PerplexityResult {
  response: string;
  citations: string[];
  latencyMs: number;
  screenshotPath?: string;
}

export async function ask(prompt: string): Promise<PerplexityResult> {
  const t0 = Date.now();
  const ctx = await getContext("perplexity");
  const page = await ctx.newPage();

  try {
    await page.goto("https://www.perplexity.ai/", {
      waitUntil: "domcontentloaded",
      timeout: 30_000,
    });

    // Wait for input to appear
    const input = await page
      .waitForSelector(SELECTORS.input, { timeout: 15_000 })
      .catch(() => page.$(SELECTORS.inputFallback));

    if (!input) {
      throw new Error("Perplexity: could not find query input field");
    }

    await input.click();
    await page.keyboard.type(prompt, { delay: 30 });
    await page.keyboard.press("Enter");

    // Wait for answer to appear and stabilise (streaming finishes)
    const response = await waitForStableAnswer(page);
    const citations = await extractCitations(page);

    return {
      response,
      citations,
      latencyMs: Date.now() - t0,
    };
  } finally {
    await page.close();
  }
}

/**
 * Poll the answer block until its text stops changing for STABLE_SETTLE_MS,
 * or until TIMEOUT_MS elapses.
 */
async function waitForStableAnswer(page: Page): Promise<string> {
  const deadline = Date.now() + TIMEOUT_MS;
  let lastText = "";
  let stableSince = 0;

  // Wait for the answer block to appear first
  await page
    .waitForSelector(SELECTORS.answerBlock, { timeout: TIMEOUT_MS })
    .catch(() => null);

  while (Date.now() < deadline) {
    const text = await extractAnswerText(page);

    if (text && text !== lastText) {
      lastText = text;
      stableSince = Date.now();
    } else if (text && stableSince > 0 && Date.now() - stableSince >= STABLE_SETTLE_MS) {
      // Text has not changed for STABLE_SETTLE_MS — streaming complete
      return text;
    }

    await sleep(POLL_INTERVAL_MS);
  }

  // Timeout — return whatever we have so far
  return lastText || "Perplexity: timed out waiting for answer";
}

async function extractAnswerText(page: Page): Promise<string> {
  return page.evaluate((selector) => {
    const els = document.querySelectorAll(selector);
    return Array.from(els)
      .map((el) => (el as HTMLElement).innerText?.trim() ?? "")
      .filter(Boolean)
      .join("\n\n");
  }, SELECTORS.answerBlock);
}

async function extractCitations(page: Page): Promise<string[]> {
  return page.evaluate((selector) => {
    const links = document.querySelectorAll(selector);
    return Array.from(links)
      .map((el) => (el as HTMLAnchorElement).href)
      .filter((href) => href.startsWith("http"));
  }, SELECTORS.citations);
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}
