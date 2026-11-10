import type { SearchSnapshot } from "../src/postings";
import { mcpSettings, siteUrl } from "../src/settings";
import type { AssetsBinding, WikiIndex } from "../src/types";
import { Wiki } from "../src/wiki";
import fixture from "./fixture/wiki_index.json";

export const index = fixture as unknown as WikiIndex;

export function makeWiki(): Wiki {
  return new Wiki(structuredClone(index), mcpSettings, siteUrl);
}

/** The deploy-time search snapshot of `source`, as scripts/build-search.ts writes it. */
export function snapshotOf(source: WikiIndex): SearchSnapshot {
  const wiki = new Wiki(structuredClone(source), mcpSettings, siteUrl);
  return wiki.searchIndex.snapshot(mcpSettings.search_version, source.generated_at);
}

/** Stand-in for the static-assets binding: the index and snapshot (null = absent) and a home page. */
export function fakeAssets(
  indexBody: unknown = index,
  searchBody: unknown = null,
): AssetsBinding & { requests: string[] } {
  const requests: string[] = [];
  return {
    requests,
    async fetch(input: Request | URL | string): Promise<Response> {
      const url = new URL(input instanceof Request ? input.url : input);
      requests.push(url.pathname);
      if (url.pathname === `/${mcpSettings.index_file}` && indexBody !== null) {
        return Response.json(indexBody);
      }
      if (url.pathname === `/${mcpSettings.search_file}` && searchBody !== null) return Response.json(searchBody);
      if (url.pathname === "/") return new Response("<html>home</html>", { headers: { "content-type": "text/html" } });
      return new Response("not found", { status: 404 });
    },
  };
}
