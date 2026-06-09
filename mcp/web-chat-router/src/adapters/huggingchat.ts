/**
 * HuggingChat adapter — anonymous mode (no login required for Phase 1).
 *
 * HuggingFace's HuggingChat (huggingface.co/chat) allows anonymous usage
 * with a limited quota. This adapter:
 *   1. Navigates to huggingface.co/chat.
 *   2. Dismisses any login/cookie banners.
 *   3. Submits the prompt in the chat input.
 *   4. Waits for the response to complete streaming.
 *   5. Returns the response text + the model name shown in the UI.
 *
 * KNOWN LIMITATION (Phase 1):
 *   - Anon mode has a lower rate limit than logged-in users (~20 queries/day per IP).
 *   - HuggingChat rotates default models; the active model name is shown in the
 *     sidebar/header. We extract it; if HF changes the selector, update SELECTORS.
 *   - If you provide WCR_HF_TOKEN in the environment, the adapter will attempt
 *     to use the logged-in session stored in the persistent profile. Phase 1 does
 *     not implement the login flow — populate the profile manually via a headed
 *     browser run (WCR_HEADLESS=0) then the cookie persists.
 */

import type { Page } from "playwright";
import { getContext } from "../browser_pool.js";

const SELECTORS = {
  // Textarea for chat input
  input: 'textarea[placeholder*="Ask"], textarea[data-testid*="chat-input"]',
  inputFallback: '[contenteditable="true"][role="textbox"]',
  // Cookie / disclaimer banner "Continue without logging in"
  anonContinue: 'button:has-text("Continue"), button:has-text("without")',
  // Response message bubbles from the assistant
  assistantMessage: '[class*="prose"], [data-role="assistant"], [class*="assistant"]',
  // Model name shown in the top header or sidebar
  modelName: '[class*="model-name"], [class*="ModelSelector"] button, header [class*="model"]',
  // Stop/cancel generation button — its disappearance signals streaming is done
  stopButton: 'button[aria-label*="Stop"], button[data-testid*="stop"]',
};

const TIMEOUT_MS = 90_000;
const STABLE_SETTLE_MS = 4_000;
const POLL_INTERVAL_MS = 1_000;

export interface HuggingChatResult {
  response: string;
  model: string;
  latencyMs: number;
}

export async function ask(prompt: string): Promise<HuggingChatResult> {
  const t0 = Date.now();
  const ctx = await getContext("huggingchat");
  const page = await ctx.newPage();

  try {
    await page.goto("https://huggingface.co/chat/", {
      waitUntil: "domcontentloaded",
      timeout: 30_000,
    });

    // Dismiss any anon/cookie notice
    await dismissBanners(page);

    // Find chat input
    let input = await page
      .waitForSelector(SELECTORS.input, { timeout: 10_000 })
      .catch(() => null);

    if (!input) {
      input = await page
        .waitForSelector(SELECTORS.inputFallback, { timeout: 10_000 })
        .catch(() => null);
    }

    if (!input) {
      throw new Error("HuggingChat: could not find chat input field");
    }

    await input.click();
    await page.keyboard.type(prompt, { delay: 20 });
    await page.keyboard.press("Enter");

    // Wait for streaming to complete
    const response = await waitForResponse(page);
    const model = await extractModelName(page);

    return {
      response,
      model: model || "HuggingChat (model unknown)",
      latencyMs: Date.now() - t0,
    };
  } finally {
    await page.close();
  }
}

async function dismissBanners(page: Page): Promise<void> {
  // Try to click "Continue without logging in" or similar anon dismiss button
  const btn = await page.$(SELECTORS.anonContinue);
  if (btn) {
    await btn.click().catch(() => undefined);
    await sleep(500);
  }
}

async function waitForResponse(page: Page): Promise<string> {
  const deadline = Date.now() + TIMEOUT_MS;
  let lastText = "";
  let stableSince = 0;

  // Wait for the first assistant message to appear
  await page
    .waitForSelector(SELECTORS.assistantMessage, { timeout: TIMEOUT_MS })
    .catch(() => null);

  while (Date.now() < deadline) {
    // Check if the stop-generation button is still present (means streaming)
    const stopVisible = await page.$(SELECTORS.stopButton);

    const text: string = await page.evaluate((sel: string) => {
      // Grab all assistant messages, take the last (most recent)
      const els = document.querySelectorAll(sel);
      const last = els[els.length - 1];
      return (last as HTMLElement)?.innerText?.trim() ?? "";
    }, SELECTORS.assistantMessage);

    if (text && text !== lastText) {
      lastText = text;
      stableSince = Date.now();
    }

    // Two stopping conditions:
    // 1. Stop button gone (streaming ended) + text is non-empty
    // 2. Text stable for STABLE_SETTLE_MS
    const textStable = Boolean(lastText) && Date.now() - stableSince >= STABLE_SETTLE_MS;
    if ((!stopVisible && lastText) || textStable) {
      return lastText;
    }

    await sleep(POLL_INTERVAL_MS);
  }

  return lastText || "HuggingChat: timed out waiting for response";
}

async function extractModelName(page: Page): Promise<string> {
  return page.evaluate((sel: string) => {
    const el = document.querySelector(sel);
    return (el as HTMLElement)?.innerText?.trim() ?? "";
  }, SELECTORS.modelName);
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}
