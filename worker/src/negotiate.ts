/**
 * Content negotiation for page URLs: a client that asks for `text/markdown` (and prefers it to
 * `text/html`) gets the page's markdown twin (`<page url>index.md`, written by step 9) in place
 * of the HTML page; every other request is answered exactly as the static assets would.
 * Responses on page URLs carry `Vary: Accept` so a cache keeps the two representations apart.
 */
import type { AssetsBinding } from "./types";

const MARKDOWN = "text/markdown";
const HTML = "text/html";
const PAGE_METHODS = new Set(["GET", "HEAD"]);

interface MediaRange {
  type: string;
  quality: number;
}

function mediaRanges(accept: string): MediaRange[] {
  return accept.split(",").map((part) => {
    const [type = "", ...params] = part.split(";").map((piece) => piece.trim());
    const q = params.map((param) => /^q=([\d.]+)$/i.exec(param)?.[1]).find((value) => value !== undefined);
    return { type: type.toLowerCase(), quality: q === undefined ? 1 : Number(q) };
  });
}

/** True when `text/markdown` is named in `Accept` with a quality above zero and is preferred to
 * `text/html` (a higher quality, or the same quality listed first). Wildcards never choose markdown. */
export function prefersMarkdown(accept: string | null): boolean {
  if (!accept) return false;
  const ranges = mediaRanges(accept);
  const markdown = ranges.findIndex((range) => range.type === MARKDOWN && range.quality > 0);
  if (markdown < 0) return false;
  const html = ranges.findIndex((range) => range.type === HTML);
  if (html < 0) return true;
  const [md, doc] = [ranges[markdown], ranges[html]];
  return !!md && !!doc && (md.quality > doc.quality || (md.quality === doc.quality && markdown < html));
}

function varyOnAccept(response: Response): Response {
  const varied = new Response(response.body, response);
  varied.headers.append("Vary", "Accept");
  return varied;
}

/** The static response for `request`, or the page's markdown twin when the client asked for it. */
export async function servePage(request: Request, assets: AssetsBinding, twinFile: string): Promise<Response> {
  const url = new URL(request.url);
  if (!PAGE_METHODS.has(request.method) || !url.pathname.endsWith("/")) return assets.fetch(request);
  if (prefersMarkdown(request.headers.get("Accept"))) {
    const twinUrl = new URL(twinFile, url);
    const twin = await assets.fetch(new Request(twinUrl, request));
    if (twin.status === 200 || twin.status === 304) {
      const response = varyOnAccept(twin);
      response.headers.set("Content-Location", twinUrl.pathname);
      return response;
    }
  }
  return varyOnAccept(await assets.fetch(request));
}
