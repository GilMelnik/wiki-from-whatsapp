import { describe, expect, it } from "vitest";
import worker from "../src/index";
import { prefersMarkdown, servePage } from "../src/negotiate";
import { twinFile } from "../src/settings";
import type { AssetsBinding } from "../src/types";

const ORIGIN = "https://wiki.test";
const PAGES: Record<string, { body: string; type: string }> = {
  "/": { body: "<html>home</html>", type: "text/html" },
  "/georgia/": { body: "<html>georgia</html>", type: "text/html" },
  [`/georgia/${twinFile}`]: { body: "# georgia", type: "text/markdown; charset=utf-8" },
  "/nope/": { body: "<html>404</html>", type: "text/html" },
};

/** Static assets: the pages above; `/nope/` is served as a 404 page with no twin. */
function assets(): AssetsBinding & { requests: string[] } {
  const requests: string[] = [];
  return {
    requests,
    async fetch(input: Request | URL | string): Promise<Response> {
      const path = new URL(input instanceof Request ? input.url : input).pathname;
      requests.push(path);
      const found = PAGES[path];
      if (!found) return new Response("<html>404</html>", { status: 404, headers: { "content-type": "text/html" } });
      const status = path === "/nope/" ? 404 : 200;
      return new Response(found.body, { status, headers: { "content-type": found.type, etag: `"${path}"` } });
    },
  };
}

const get = (path: string, accept?: string, method = "GET"): Request =>
  new Request(`${ORIGIN}${path}`, { method, headers: accept ? { Accept: accept } : {} });

describe("prefersMarkdown", () => {
  it.each([
    ["text/markdown", true],
    ["text/markdown, text/html", true],
    ["text/markdown, text/html, */*", true],
    ["TEXT/Markdown;q=0.9, text/html;q=0.5", true],
    ["text/html;q=0.8, text/markdown", true],
    ["text/html, text/markdown", false],
    ["text/markdown;q=0.5, text/html", false],
    ["text/markdown;q=0", false],
    ["text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", false],
    ["*/*", false],
    ["text/*", false],
    ["application/json", false],
    ["", false],
  ])("%j -> %s", (accept, expected) => {
    expect(prefersMarkdown(accept)).toBe(expected);
  });

  it("is false without an Accept header", () => {
    expect(prefersMarkdown(null)).toBe(false);
  });
});

describe("servePage", () => {
  it("answers a markdown request on a page URL with the twin, and says it varies on Accept", async () => {
    const response = await servePage(get("/georgia/", "text/markdown"), assets(), twinFile);
    expect(response.status).toBe(200);
    expect(await response.text()).toBe("# georgia");
    expect(response.headers.get("content-type")).toBe("text/markdown; charset=utf-8");
    expect(response.headers.get("vary")).toBe("Accept");
    expect(response.headers.get("content-location")).toBe(`/georgia/${twinFile}`);
  });

  it("answers the home page's markdown request with the root twin", async () => {
    const stub = assets();
    await servePage(get("/", "text/markdown"), stub, twinFile);
    expect(stub.requests).toEqual([`/${twinFile}`, "/"]); // no root twin in this fake: falls back to the page
  });

  it.each([
    ["a browser", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"],
    ["curl", "*/*"],
    ["no Accept header", undefined],
  ])("leaves the request of %s alone (HTML, Vary: Accept)", async (_who, accept) => {
    const stub = assets();
    const response = await servePage(get("/georgia/", accept), stub, twinFile);
    expect(await response.text()).toBe("<html>georgia</html>");
    expect(response.headers.get("content-type")).toBe("text/html");
    expect(response.headers.get("vary")).toBe("Accept");
    expect(stub.requests).toEqual(["/georgia/"]);
  });

  it("behaves as today for an unknown page: the 404 page, not an error", async () => {
    const response = await servePage(get("/nope/", "text/markdown"), assets(), twinFile);
    expect(response.status).toBe(404);
    expect(await response.text()).toBe("<html>404</html>");
    expect(response.headers.get("content-type")).toBe("text/html");
  });

  it("passes a path that is not a page URL straight to the assets", async () => {
    const stub = assets();
    const response = await servePage(get("/georgia", "text/markdown"), stub, twinFile);
    expect(response.status).toBe(404);
    expect(response.headers.get("vary")).toBeNull();
    expect(stub.requests).toEqual(["/georgia"]);
  });

  it("leaves other methods alone", async () => {
    const stub = assets();
    await servePage(get("/georgia/", "text/markdown", "POST"), stub, twinFile);
    expect(stub.requests).toEqual(["/georgia/"]);
  });

  it("serves a HEAD request for the twin without a body", async () => {
    const response = await servePage(get("/georgia/", "text/markdown", "HEAD"), assets(), twinFile);
    expect(response.headers.get("vary")).toBe("Accept");
    expect(response.headers.get("content-type")).toBe("text/markdown; charset=utf-8");
  });
});

describe("the Worker entry", () => {
  it("negotiates page URLs and leaves the other paths to the assets", async () => {
    const env = { ASSETS: assets() };
    const markdown = await worker.fetch(get("/georgia/", "text/markdown"), env);
    expect(await markdown.text()).toBe("# georgia");
    const html = await worker.fetch(get("/georgia/", "text/html"), env);
    expect(await html.text()).toBe("<html>georgia</html>");
    expect(html.headers.get("vary")).toBe("Accept");
  });
});
