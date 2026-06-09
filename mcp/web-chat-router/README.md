# web-chat-router — MCP server (Tier W free vote)

A local MCP server that gives the **Adjudicator** role a free 3-way opinion from
frontier models without spending Codex pool quota or paid API credits.

## Phase 1 scope (this implementation)

Three adapters, all **no-login / anonymous**:

| Provider | Mode | Notes |
|----------|------|-------|
| **Perplexity** | Web search + answer | Extracts citations. Anon rate limit ~5 queries/5 min per IP. |
| **LMArena** | Anonymous side-by-side arena | Returns both Model A + B responses. Model pair is random (non-deterministic). |
| **HuggingChat** | HuggingFace chat anon mode | Returns response + model name shown in UI. ~20 queries/day per IP anon. |

## Phase 2 (future — login automation)

- Claude.ai web (requires account login flow via Playwright)
- ChatGPT web (same)
- Gemini web (same)

Login automation: automated cookie injection from a managed profile directory.
Account farming policy: one dedicated account per provider, no throwaway stacking.

## Phase 3 (future — account farm)

Not planned unless Phase 1/2 quota limits are insufficient for the pipeline cadence.

## Privacy guard (cannot be disabled)

The `redact.ts` guard runs **before any browser is launched**. It blocks prompts
containing:

- API key patterns (`sk-*`, `AKIA*`, `AIza*`, `Bearer <token>`)
- Absolute filesystem paths (`/Users/`, `C:\Users\`, `/home/`)
- Client brand identifiers (`client_name`, `brand/`, `assets/proprietary/`)

Do **not** paste client footage descriptions, brand voice docs, or script drafts
containing real client names into these adapters.

## Install

```bash
# Mac/Linux
bash mcp/web-chat-router/install.sh

# Windows (PowerShell)
.\mcp\web-chat-router\install.ps1
```

Requirements: Node.js >= 20, internet access (Playwright downloads Chromium ~150 MB).

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `WCR_HEADLESS` | `1` | Set to `0` to run browser visibly (debug mode) |
| `WCR_PROFILE_DIR` | system temp | Base directory for persistent browser profiles |
| `WCR_QUOTA_PER_HOUR` | `10` | Soft call limit per provider per hour |
| `PROJECT_LOG_DIR` | (none) | Path to project `logs/` dir for devlog.sqlite writes |

## MCP tools

### `web_chat.ask`

Single provider query.

```json
{
  "provider": "perplexity",
  "prompt": "What are the top 3 TikTok hooks for SaaS product videos in 2026?"
}
```

Returns JSON: `{ provider, response, latency_ms, ...provider_metadata }`.

For `lmarena`, the response includes both Model A + B, and metadata contains
`modelA`, `modelB`, `responseA`, `responseB`.

### `web_chat.compare`

Send the same prompt to multiple providers sequentially.

```json
{
  "prompt": "Rate this hook: 'You've been doing analytics wrong.'",
  "providers": ["perplexity", "lmarena", "huggingchat"]
}
```

Returns JSON: `{ prompt_summary, results: { perplexity: {...}, lmarena: {...}, huggingchat: {...} } }`.

## Manual test (no MCP client needed)

```bash
# Start the server
npx tsx src/server.ts

# Send a tool call (in another terminal or via stdin pipe)
echo '{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "web_chat.ask",
    "arguments": { "provider": "huggingchat", "prompt": "What is 2+2?" }
  }
}' | npx tsx src/server.ts
```

## Quota tracking

Call counts are persisted in `eval/web_chat_quota.json` (per-provider, per-hour
sliding window). This file is reset automatically each hour. The soft limit
prevents runaway loops but does NOT prevent a deliberate burst — monitor the file
if running the pipeline frequently.

## Known limitations (Phase 1)

1. **LMArena model pair is random.** You cannot request a specific model without
   logging in and using Direct Chat mode with model selection. Phase 2 will add
   login support.

2. **DOM selectors will drift.** Perplexity / LMArena / HuggingChat update their
   UIs frequently. When an adapter breaks, update the `SELECTORS` block at the
   top of the relevant `src/adapters/*.ts` file.

3. **Anon rate limits are IP-based.** If the pipeline runs on a shared or cloud
   IP, the quota may be exhausted by other users. Run locally or behind a
   residential proxy.

4. **No parallel provider queries.** `web_chat.compare` runs providers
   sequentially to reduce fingerprint correlation risk. This adds latency
   (~30-90s per provider). Parallelism may be added in Phase 2 with separate
   proxy IPs.

5. **Perplexity may require CAPTCHA** on new IPs. The persistent profile helps
   after the first successful session. If CAPTCHA appears, run headed once
   (`WCR_HEADLESS=0`) to solve it manually.

6. **`better-sqlite3` is not listed as a dependency** in package.json by default
   because the MCP server optionally falls back gracefully when the DB is
   unavailable. If you want guaranteed devlog writes, add `better-sqlite3` as
   a runtime dep and rebuild.
