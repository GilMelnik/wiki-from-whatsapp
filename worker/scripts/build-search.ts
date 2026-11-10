/**
 * Deploy-time step (wrangler.jsonc build.command): read <site>/wiki_index.json and write the
 * search snapshot <site>/wiki_search.json with the same code the Worker searches with, so a cold
 * isolate only parses it instead of indexing every page. No index yet -> warn and exit 0 (the
 * static site still deploys; /mcp then answers 503 until step 9 ships an index).
 * Usage: tsx scripts/build-search.ts <site dir>
 */
/// <reference types="node" />
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { mcpSettings, siteUrl } from "../src/settings";
import type { WikiIndex } from "../src/types";
import { Wiki } from "../src/wiki";

const siteDir = process.argv[2];
if (!siteDir) throw new Error("usage: build-search <site dir>");
const indexPath = join(siteDir, mcpSettings.index_file);
const searchPath = join(siteDir, mcpSettings.search_file);

if (!existsSync(indexPath)) {
  console.warn(`build-search: ${indexPath} not found; no search snapshot written.`);
} else {
  const started = performance.now();
  const index = JSON.parse(readFileSync(indexPath, "utf8")) as WikiIndex;
  const wiki = new Wiki(index, mcpSettings, siteUrl);
  const snapshot = wiki.searchIndex.snapshot(mcpSettings.search_version, index.generated_at);
  writeFileSync(searchPath, `${JSON.stringify(snapshot, null, 2)}\n`, "utf8");
  const ms = Math.round(performance.now() - started);
  console.log(`build-search: ${index.pages.length} pages, ${snapshot.terms.length} terms -> ${searchPath} (${ms} ms)`);
}
