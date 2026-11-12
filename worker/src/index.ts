/**
 * Cloudflare Worker entry. `wrangler.jsonc` sends `/mcp` and the page URLs (the home page and
 * every path ending in `/`) to this script (`assets.run_worker_first`); the other files are
 * served from the static site without it. A page URL is answered by the static assets, or with
 * the page's markdown twin when the client asks for `text/markdown` (see `negotiate.ts`).
 * The handler is stateless (MCP 2026-07-28 per-request envelope, plus the SDK's stateless
 * fallback for 2025-era clients); the wiki index is fetched from the assets once per isolate.
 */
import { createMcpHandler, originValidationResponse, type McpHttpHandler } from "@modelcontextprotocol/server";
import { servePage } from "./negotiate";
import { mcpSettings, siteUrl, twinFile } from "./settings";
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
    if (new URL(request.url).pathname !== mcpSettings.route) return servePage(request, env.ASSETS, twinFile);
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
