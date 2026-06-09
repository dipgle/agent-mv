/**
 * Devlog helper for web-chat-router MCP server.
 *
 * Mirrors the Python pattern in orchestrator/lib/devlog.py.
 * Writes kind='web_chat_call' events into the project devlog SQLite DB.
 *
 * The DB path is resolved from the MCP env var PROJECT_LOG_DIR (set by
 * the MCP server registration in .mcp.json) so it writes to the same
 * devlog.sqlite as the Python orchestrator.
 *
 * better-sqlite3 is an optional runtime dependency. If not installed,
 * logging silently no-ops so the server still functions.
 */

import { createHash } from "crypto";
import { createRequire } from "module";
import { join } from "path";
import { existsSync } from "fs";

// createRequire lets us load CJS modules (like better-sqlite3) from ESM context.
const _require = createRequire(import.meta.url);

// Resolved once at module load time.
const LOG_DIR = process.env.PROJECT_LOG_DIR ?? "";
const DEVLOG_PATH = LOG_DIR ? join(LOG_DIR, "devlog.sqlite") : "";

// Lazy-loaded better-sqlite3 database instance (null when module not installed).
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let _db: any = null;
let _dbResolved = false;

function promptHash(prompt: string): string {
  return createHash("sha256").update(prompt).digest("hex").slice(0, 16);
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function getDb(): any | null {
  if (_dbResolved) return _db;
  _dbResolved = true;

  if (!DEVLOG_PATH || !existsSync(DEVLOG_PATH)) return null;

  try {
    // Use createRequire-based require so better-sqlite3 (CJS) loads in ESM.
    const BetterSqlite = _require("better-sqlite3");
    _db = new BetterSqlite(DEVLOG_PATH);
    return _db;
  } catch {
    // better-sqlite3 not installed or DB open failed — silently skip logging.
    return null;
  }
}

export interface WebChatCallEvent {
  provider: string;
  promptHash?: string;
  latencyMs: number;
  responseLen: number;
  model?: string;      // model name shown by the provider (if available)
  blocked?: boolean;   // true when privacy guard fired
  error?: string;      // error message if call failed
}

/**
 * Append a web_chat_call event to devlog.sqlite.
 * Silently no-ops if the DB is not available (PROJECT_LOG_DIR unset or
 * DB missing or better-sqlite3 not installed).
 */
export function logWebChatCall(prompt: string, ev: WebChatCallEvent): void {
  const db = getDb();
  if (!db) return;

  const content = JSON.stringify({
    provider: ev.provider,
    prompt_hash: ev.promptHash ?? promptHash(prompt),
    latency_ms: ev.latencyMs,
    response_len: ev.responseLen,
    model: ev.model ?? null,
    blocked: ev.blocked ?? false,
    error: ev.error ?? null,
  });

  try {
    db.prepare(
      `INSERT INTO events (ts, kind, actor, ref_type, ref_id, content)
       VALUES (datetime('now'), 'web_chat_call', 'web_chat_router', 'system', '', ?)`
    ).run(content);
  } catch {
    // Best-effort — never crash the adapter due to logging failure.
  }
}
