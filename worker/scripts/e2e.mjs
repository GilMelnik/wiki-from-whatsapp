// End-to-end check of a running wiki MCP endpoint with the official MCP client, in both
// protocol eras (2025 initialize handshake and 2026-07-28 per-request envelope).
// Usage: npm run e2e -- http://127.0.0.1:8799/mcp   (expects the synthetic fixture index)
import { Client, StreamableHTTPClientTransport } from "@modelcontextprotocol/client";

const endpoint = new URL(process.argv[2] ?? "http://127.0.0.1:8799/mcp");
const CALLS = [
  ["search", { query: "בקולומביה", limit: 3 }],
  ["search", { query: '"צו הורות"' }],
  ["get_page", { id: "Colombia" }],
  ["get_links", { id: "colombia" }],
  ["list_pages", { view: "tree" }],
  ["list_labels", { kind: "country" }],
  ["get_page", { id: "no-such-page" }],
];

function check(condition, message) {
  if (!condition) throw new Error(`e2e check failed: ${message}`);
}

async function staticChecks() {
  const home = await fetch(new URL("/", endpoint));
  check(home.ok && (await home.text()).includes("<html"), `GET / returned ${home.status}`);
  const get = await fetch(endpoint);
  check(get.status === 405, `GET ${endpoint.pathname} returned ${get.status}, expected 405`);
  console.log(`static: GET / -> ${home.status} html, GET ${endpoint.pathname} -> ${get.status}`);
}

async function session(versionNegotiation) {
  const client = new Client({ name: "wiki-mcp-e2e", version: "1.0.0" }, versionNegotiation ? { versionNegotiation } : {});
  await client.connect(new StreamableHTTPClientTransport(endpoint));
  const era = client.getNegotiatedProtocolVersion();
  const { tools } = await client.listTools();
  console.log(`\n== protocol ${era}, server ${client.getServerVersion()?.name}, tools: ${tools.map((t) => t.name).join(", ")}`);
  check(tools.length === 5, `expected 5 tools, got ${tools.length}`);
  for (const [name, args] of CALLS) {
    const result = await client.callTool({ name, arguments: args });
    const first = result.content[0]?.text ?? "";
    console.log(`${name} ${JSON.stringify(args)} -> ${result.isError ? "ERROR " : ""}${first.slice(0, 300)}${first.length > 300 ? "…" : ""}`);
    check(Boolean(result.isError) === (args.id === "no-such-page"), `${name} isError=${result.isError}`);
  }
  await client.close();
  return era;
}

await staticChecks();
const eras = [await session(undefined), await session({ mode: { pin: "2026-07-28" } }), await session({ mode: "auto" })];
check(eras[0] !== "2026-07-28" && eras[1] === "2026-07-28" && eras[2] === "2026-07-28", `eras ${eras}`);
console.log(`\nOK: eras ${eras.join(", ")}`);
