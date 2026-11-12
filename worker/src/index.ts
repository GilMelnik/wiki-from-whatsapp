/**
 * Cloudflare Worker entry. `wrangler.jsonc` sends only `/mcp` to this script
 * (`assets.run_worker_first`); every other path is served from the static site without it.
 * The handler is stateless (MCP 2026-07-28 per-request envelope, plus the SDK's stateless
 * fallback for 2025-era clients); the wiki index is fetched from the assets once per isolate.
 */
import { createMcpHandler, originValidationResponse, type McpHttpHandler } from "@modelcontextprotocol/server";
import { mcpSettings, siteUrl } from "./settings";
import { createServer } from "./tools";
import type { Env } from "./types";
import { Wiki } from "./wiki";

let handlerPromise: Promise<McpHttpHandler> | undefined;

async function buildHandler(env: Env): Promise<McpHttpHandler> {
  const wiki = await Wiki.load(env.ASSETS, mcpSettings, siteUrl);
  return createMcpHandler(() => createServer(wiki, mcpSettings), {
    onerror: (error) => console.error(`MCP request failed: ${error.message}`),
  });
}

function unavailable(error: unknown): Response {
  const message = error instanceof Error ? error.message : String(error);
  console.error(`Wiki index unavailable: ${message}`);
  const body = { jsonrpc: "2.0", id: null, error: { code: -32603, message: `Wiki index unavailable: ${message}` } };
  return Response.json(body, { status: 503 });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    if (new URL(request.url).pathname !== mcpSettings.route) return env.ASSETS.fetch(request);
    const rejected = originValidationResponse(request, mcpSettings.allowed_origin_hostnames);
    if (rejected) return rejected;
    handlerPromise ??= buildHandler(env);
    let handler: McpHttpHandler;
    try {
      handler = await handlerPromise;
    } catch (error) {
      handlerPromise = undefined;
      return unavailable(error);
    }
    return handler.fetch(request);
  },
};
