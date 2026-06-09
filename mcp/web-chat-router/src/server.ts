/**
 * Web Chat Router — MCP server entry point (Phase 1).
 *
 * Exposes two MCP tools:
 *   web_chat.ask      — single provider query
 *   web_chat.compare  — 3-way vote (Perplexity + LMArena + HuggingChat)
 *
 * Design constraints (Phase 1):
 *   - No Claude.ai / ChatGPT / Gemini adapters (those need login, Phase 2)
 *   - Privacy guard fires BEFORE any browser launch (cannot be disabled)
 *   - Soft quota: 10 calls/hour per provider, tracked in eval/web_chat_quota.json
 *   - Free tier: cost_gate treats web_chat/* as cost=0
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
  type Tool,
} from "@modelcontextprotocol/sdk/types.js";

import { assertSafePrompt, BlockedPromptError } from "./redact.js";
import { closeAll } from "./browser_pool.js";
import { logWebChatCall } from "./devlog.js";
import { checkQuota, recordCall } from "./quota.js";
import * as perplexity from "./adapters/perplexity.js";
import * as lmarena from "./adapters/lmarena.js";
import * as huggingchat from "./adapters/huggingchat.js";

// ─── Tool schemas ──────────────────────────────────────────────────────────

const ASK_TOOL: Tool = {
  name: "web_chat.ask",
  description:
    "Send a prompt to a single free web chat provider (no login, no API key). " +
    "Returns the response text plus optional metadata (citations, model name). " +
    "PRIVACY: prompts containing API keys, absolute paths, or client brand markers " +
    "are blocked before any browser interaction.",
  inputSchema: {
    type: "object",
    properties: {
      provider: {
        type: "string",
        enum: ["perplexity", "lmarena", "huggingchat"],
        description:
          "Which provider to query. " +
          "perplexity = web search + answer with citations; " +
          "lmarena = anonymous side-by-side arena (returns both model A + B); " +
          "huggingchat = HuggingFace chat (anon mode).",
      },
      prompt: {
        type: "string",
        description: "The question or task to send. Must NOT contain API keys, file paths, or client brand assets.",
      },
    },
    required: ["provider", "prompt"],
  },
};

const COMPARE_TOOL: Tool = {
  name: "web_chat.compare",
  description:
    "Send the same prompt to multiple providers and return all responses for comparison. " +
    "Useful for the Adjudicator role: get a free 3-way vote without burning Codex pool quota. " +
    "Runs providers sequentially (not parallel) to avoid fingerprint correlation. " +
    "PRIVACY: same blocking rules as web_chat.ask apply.",
  inputSchema: {
    type: "object",
    properties: {
      prompt: {
        type: "string",
        description: "The question or task to send to all providers.",
      },
      providers: {
        type: "array",
        items: { type: "string", enum: ["perplexity", "lmarena", "huggingchat"] },
        description: "List of providers to query. Defaults to all three if omitted.",
        default: ["perplexity", "lmarena", "huggingchat"],
      },
    },
    required: ["prompt"],
  },
};

// ─── Provider dispatch ─────────────────────────────────────────────────────

type ProviderName = "perplexity" | "lmarena" | "huggingchat";

interface AskResult {
  response: string;
  latencyMs: number;
  metadata: Record<string, unknown>;
}

async function dispatchAsk(provider: ProviderName, prompt: string): Promise<AskResult> {
  const t0 = Date.now();
  let responseText = "";
  let metadata: Record<string, unknown> = {};

  switch (provider) {
    case "perplexity": {
      const r = await perplexity.ask(prompt);
      responseText = r.response;
      metadata = { citations: r.citations };
      break;
    }
    case "lmarena": {
      const r = await lmarena.ask(prompt);
      // Flatten both sides into a structured response string
      responseText =
        `[Model A — ${r.modelA.name}]\n${r.modelA.response}\n\n` +
        `[Model B — ${r.modelB.name}]\n${r.modelB.response}`;
      metadata = {
        modelA: r.modelA.name,
        modelB: r.modelB.name,
        responseA: r.modelA.response,
        responseB: r.modelB.response,
      };
      break;
    }
    case "huggingchat": {
      const r = await huggingchat.ask(prompt);
      responseText = r.response;
      metadata = { model: r.model };
      break;
    }
    default:
      throw new Error(`Unknown provider: ${provider}`);
  }

  return {
    response: responseText,
    latencyMs: Date.now() - t0,
    metadata,
  };
}

// ─── Tool handlers ─────────────────────────────────────────────────────────

async function handleAsk(args: Record<string, unknown>) {
  const provider = String(args.provider) as ProviderName;
  const prompt = String(args.prompt);

  // 1. Privacy guard — MUST run before browser launch
  try {
    assertSafePrompt(prompt);
  } catch (err) {
    if (err instanceof BlockedPromptError) {
      logWebChatCall(prompt, {
        provider,
        promptHash: "",
        latencyMs: 0,
        responseLen: 0,
        blocked: true,
        error: err.message,
      });
      return {
        content: [{ type: "text", text: `BLOCKED: ${err.message}` }],
        isError: true,
      };
    }
    throw err;
  }

  // 2. Soft quota check
  const quotaOk = await checkQuota(provider);
  if (!quotaOk) {
    return {
      content: [{
        type: "text",
        text: `QUOTA: provider '${provider}' has exceeded 10 calls/hour soft limit. ` +
              `Wait or use a different provider.`,
      }],
      isError: true,
    };
  }

  // 3. Dispatch to adapter
  let result: AskResult;
  try {
    result = await dispatchAsk(provider, prompt);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    logWebChatCall(prompt, {
      provider,
      promptHash: "",
      latencyMs: 0,
      responseLen: 0,
      error: msg,
    });
    return {
      content: [{ type: "text", text: `ERROR [${provider}]: ${msg}` }],
      isError: true,
    };
  }

  // 4. Record quota usage + devlog
  await recordCall(provider);
  logWebChatCall(prompt, {
    provider,
    promptHash: "",
    latencyMs: result.latencyMs,
    responseLen: result.response.length,
    model: typeof result.metadata.model === "string" ? result.metadata.model : undefined,
  });

  return {
    content: [{
      type: "text",
      text: JSON.stringify({
        provider,
        response: result.response,
        latency_ms: result.latencyMs,
        ...result.metadata,
      }, null, 2),
    }],
  };
}

async function handleCompare(args: Record<string, unknown>) {
  const prompt = String(args.prompt);
  const providers: ProviderName[] =
    Array.isArray(args.providers) && args.providers.length > 0
      ? (args.providers as ProviderName[])
      : ["perplexity", "lmarena", "huggingchat"];

  // 1. Privacy guard
  try {
    assertSafePrompt(prompt);
  } catch (err) {
    if (err instanceof BlockedPromptError) {
      return {
        content: [{ type: "text", text: `BLOCKED: ${err.message}` }],
        isError: true,
      };
    }
    throw err;
  }

  // 2. Run providers sequentially to avoid fingerprint correlation
  const results: Record<string, unknown> = {};
  for (const provider of providers) {
    const quotaOk = await checkQuota(provider);
    if (!quotaOk) {
      results[provider] = { error: "quota_exceeded" };
      continue;
    }

    try {
      const r = await dispatchAsk(provider, prompt);
      await recordCall(provider);
      logWebChatCall(prompt, {
        provider,
        promptHash: "",
        latencyMs: r.latencyMs,
        responseLen: r.response.length,
        model: typeof r.metadata.model === "string" ? r.metadata.model : undefined,
      });
      results[provider] = {
        response: r.response,
        latency_ms: r.latencyMs,
        ...r.metadata,
      };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      results[provider] = { error: msg };
    }
  }

  return {
    content: [{
      type: "text",
      text: JSON.stringify({ prompt_summary: prompt.slice(0, 80), results }, null, 2),
    }],
  };
}

// ─── MCP server bootstrap ──────────────────────────────────────────────────

const server = new Server(
  { name: "web-chat-router", version: "1.0.0" },
  { capabilities: { tools: {} } }
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [ASK_TOOL, COMPARE_TOOL],
}));

server.setRequestHandler(CallToolRequestSchema, async (req) => {
  const { name, arguments: args } = req.params;
  const safeArgs = (args ?? {}) as Record<string, unknown>;

  if (name === "web_chat.ask") return handleAsk(safeArgs);
  if (name === "web_chat.compare") return handleCompare(safeArgs);

  return {
    content: [{ type: "text", text: `Unknown tool: ${name}` }],
    isError: true,
  };
});

// Graceful shutdown
process.on("SIGINT", async () => { await closeAll(); process.exit(0); });
process.on("SIGTERM", async () => { await closeAll(); process.exit(0); });

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  // Server now listens on stdin/stdout — keep alive until killed
}

main().catch((err) => {
  console.error("web-chat-router: fatal error", err);
  process.exit(1);
});
