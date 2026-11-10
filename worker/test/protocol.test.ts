/**
 * Protocol-level test: the official MCP client talks to the Worker's fetch handler (no network),
 * once with the 2025 initialize handshake (the client's default) and once pinned to 2026-07-28.
 */
import { Client, StreamableHTTPClientTransport, type CallToolResult } from "@modelcontextprotocol/client";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { mcpSettings } from "../src/settings";
import type { Env } from "../src/types";
import { fakeAssets, index, snapshotOf } from "./helpers";

const ORIGIN = "http://localhost:8799";
const MCP_URL = `${ORIGIN}${mcpSettings.route}`;
const TOOLS = ["search", "get_page", "list_pages", "get_links", "list_labels"];

type Worker = { fetch(request: Request, env: Env): Promise<Response> };

async function freshWorker(): Promise<Worker> {
  vi.resetModules();
  return (await import("../src/index")).default;
}

async function connect(worker: Worker, env: Env, modern: boolean): Promise<Client> {
  const transport = new StreamableHTTPClientTransport(new URL(MCP_URL), {
    fetch: (input, init) => worker.fetch(new Request(input, init), env),
  });
  const client = new Client(
    { name: "wiki-mcp-test", version: "1.0.0" },
    modern ? { versionNegotiation: { mode: { pin: "2026-07-28" } } } : {},
  );
  await client.connect(transport);
  return client;
}

const text = (result: unknown, block = 0): string => {
  const content = (result as CallToolResult).content[block];
  return content?.type === "text" ? content.text : "";
};
const json = (result: unknown): Record<string, unknown> => JSON.parse(text(result)) as Record<string, unknown>;

for (const modern of [false, true]) {
  describe(`MCP over HTTP (${modern ? "2026-07-28" : "2025 initialize"})`, () => {
    let client: Client;

    beforeEach(async () => {
      client = await connect(await freshWorker(), { ASSETS: fakeAssets(index, snapshotOf(index)) }, modern);
    });

    it("identifies the server and its instructions", () => {
      expect(client.getServerVersion()?.name).toBe(mcpSettings.server.name);
      expect(client.getInstructions()).toContain("not legal, medical or financial advice");
      expect(client.getNegotiatedProtocolVersion()).toBe(modern ? "2026-07-28" : "2025-11-25");
    });

    it("lists the five read-only tools with input schemas", async () => {
      const { tools } = await client.listTools();
      expect(tools.map((t) => t.name)).toEqual(TOOLS);
      for (const tool of tools) {
        expect(tool.annotations?.readOnlyHint).toBe(true);
        expect(tool.inputSchema.type).toBe("object");
        expect(tool.description?.length).toBeGreaterThan(80);
      }
    });

    it("calls every tool and chains ids from one to the next", async () => {
      const found = json(await client.callTool({ name: "search", arguments: { query: "בקולומביה", limit: 3 } }));
      const first = (found.hits as { id: string }[])[0];
      expect(first?.id).toBe("colombia");

      const page = await client.callTool({ name: "get_page", arguments: { id: first?.id ?? "" } });
      expect(json(page).url).toBe("https://gaysurrogacy.co.il/colombia/");
      expect(text(page, 1)).toContain("[[mexico|מקסיקו]]");

      const links = json(await client.callTool({ name: "get_links", arguments: { id: "Colombia" } }));
      expect((links.neighbours as { id: string }[]).map((n) => n.id)).toContain("mexico");

      const tree = json(await client.callTool({ name: "list_pages", arguments: {} }));
      expect(tree.count).toBe(8);
      const flat = json(await client.callTool({ name: "list_pages", arguments: { view: "flat", label: "usa" } }));
      expect(flat.pages).toEqual([expect.objectContaining({ id: "usa" })]);

      const labels = json(await client.callTool({ name: "list_labels", arguments: { kind: "country" } }));
      expect((labels.labels as unknown[]).length).toBe(4);
    });

    it("returns tool errors the model can act on", async () => {
      const missing = await client.callTool({ name: "get_page", arguments: { id: "no-such-page" } });
      expect(missing.isError).toBe(true);
      expect(text(missing)).toMatch(/No page with id/);
      const badLabel = await client.callTool({ name: "search", arguments: { query: "x", label: "nope" } });
      expect(badLabel.isError).toBe(true);
      const badArgs = await client.callTool({ name: "search", arguments: { query: "x", limit: 999 } });
      expect(badArgs.isError).toBe(true);
    });
  });
}

describe("HTTP routing", () => {
  it("passes every other path to the static assets", async () => {
    const assets = fakeAssets();
    const response = await (await freshWorker()).fetch(new Request(`${ORIGIN}/`), { ASSETS: assets });
    expect(await response.text()).toBe("<html>home</html>");
    expect(assets.requests).toEqual(["/"]);
  });

  it("refuses GET on the endpoint (stateless server: no standalone stream)", async () => {
    const response = await (await freshWorker()).fetch(new Request(MCP_URL), { ASSETS: fakeAssets() });
    expect(response.status).toBe(405);
  });

  it("rejects a foreign browser Origin", async () => {
    const request = new Request(MCP_URL, {
      method: "POST",
      headers: { origin: "https://evil.example", "content-type": "application/json" },
      body: "{}",
    });
    const response = await (await freshWorker()).fetch(request, { ASSETS: fakeAssets() });
    expect(response.status).toBe(403);
  });

  it("answers 503 with the reason when the index is missing, and retries on the next request", async () => {
    const worker = await freshWorker();
    const post = () =>
      new Request(MCP_URL, {
        method: "POST",
        headers: { "content-type": "application/json", accept: "application/json, text/event-stream" },
        body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/list" }),
      });
    const missing = await worker.fetch(post(), { ASSETS: fakeAssets(null) });
    expect(missing.status).toBe(503);
    expect(await missing.text()).toMatch(/not in the static assets/);
    const assets = fakeAssets();
    await worker.fetch(post(), { ASSETS: assets });
    expect(assets.requests).toEqual([`/${mcpSettings.index_file}`, `/${mcpSettings.search_file}`]);
  });
});
