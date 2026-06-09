/**
 * LMArena adapter — no login required.
 *
 * LMArena (lmarena.ai) presents two anonymous model responses and asks the
 * user to vote. This adapter:
 *   1. Navigates to lmarena.ai.
 *   2. Submits the prompt.
 *   3. Waits for both model A and model B responses to complete streaming.
 *   4. Returns both responses + the model names (revealed in column headers).
 *
 * KNOWN LIMITATION (Phase 1):
 *   - The two models selected are random per session; there is no way to
 *     request a specific model pairing without logging in.
 *   - Model names may not be revealed until after both responses appear.
 *   - If LMArena changes their DOM, update SELECTORS below.
 */

import type { Page } from "playwright";
import { getContext } from "../browser_pool.js";

// DOM selectors — update when LMArena redesigns their UI
const SELECTORS = {
  // Prompt input
  input: 'textarea[placeholder*="Enter"], textarea[placeholder*="Ask"], textarea[placeholder*="Send"]',
  inputFallback: '[contenteditable="true"]',
  // Catch-all: any bot/assistant message text block in the arena layout
  anyResponse: '.bot-row .message-content, [class*="bot-message"], [class*="assistant-message"], [data-role="assistant"]',
  // Model name labels shown in column headers
  modelLabel: '[class*="model-name"], [class*="model-label"], [id*="model-name"]',
};

const TIMEOUT_MS = 90_000;
const STABLE_SETTLE_MS = 4_000;
const POLL_INTERVAL_MS = 1_000;

export interface LMArenaResult {
  modelA: { name: string; response: string };
  modelB: { name: string; response: string };
  latencyMs: number;
}

export async function ask(prompt: string): Promise<LMArenaResult> {
  const t0 = Date.now();
  const ctx = await getContext("lmarena");
  const page = await ctx.newPage();

  try {
    await page.goto("https://lmarena.ai/", {
      waitUntil: "domcontentloaded",
      timeout: 30_000,
    });

    // Find and fill the prompt input
    let input = await page.$(SELECTORS.input);
    if (!input) {
      input = await page
        .waitForSelector(SELECTORS.inputFallback, { timeout: 15_000 })
        .catch(() => null);
    }
    if (!input) {
      throw new Error("LMArena: could not find prompt input field");
    }

    await input.click();
    await page.keyboard.type(prompt, { delay: 25 });
    await page.keyboard.press("Enter");

    // Wait for responses to settle
    const [responseA, responseB] = await waitForBothResponses(page);
    const [nameA, nameB] = await extractModelNames(page);

    return {
      modelA: { name: nameA || "Model A (unknown)", response: responseA },
      modelB: { name: nameB || "Model B (unknown)", response: responseB },
      latencyMs: Date.now() - t0,
    };
  } finally {
    await page.close();
  }
}

async function waitForBothResponses(page: Page): Promise<[string, string]> {
  const deadline = Date.now() + TIMEOUT_MS;
  let lastA = "";
  let lastB = "";
  let stableA = 0;
  let stableB = 0;

  // Wait for at least one response to appear
  await page
    .waitForSelector(SELECTORS.anyResponse, { timeout: TIMEOUT_MS })
    .catch(() => null);

  while (Date.now() < deadline) {
    const texts: string[] = await page.evaluate((sel: string) => {
      const els = document.querySelectorAll(sel);
      return Array.from(els)
        .map((el) => (el as HTMLElement).innerText?.trim() ?? "")
        .filter(Boolean);
    }, SELECTORS.anyResponse);

    const a = texts[0] ?? "";
    const b = texts[1] ?? "";
    const now = Date.now();

    if (a !== lastA) { lastA = a; stableA = now; }
    if (b !== lastB) { lastB = b; stableB = now; }

    const aStable = Boolean(a) && now - stableA >= STABLE_SETTLE_MS;
    const bStable = Boolean(b) && now - stableB >= STABLE_SETTLE_MS;

    if (aStable && bStable) {
      return [a, b];
    }

    await sleep(POLL_INTERVAL_MS);
  }

  return [
    lastA || "LMArena Model A: timed out",
    lastB || "LMArena Model B: timed out",
  ];
}

async function extractModelNames(page: Page): Promise<[string, string]> {
  const names: string[] = await page.evaluate((sel: string) => {
    const els = document.querySelectorAll(sel);
    return Array.from(els)
      .slice(0, 2)
      .map((el) => (el as HTMLElement).innerText?.trim() ?? "");
  }, SELECTORS.modelLabel);

  return [names[0] ?? "", names[1] ?? ""];
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}
