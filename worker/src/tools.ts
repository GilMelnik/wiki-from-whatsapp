/**
 * The MCP server: five read-only tools over one loaded Wiki. Results are compact JSON text
 * (get_page adds the markdown body, opened by the site's disclaimer line, as a second block); every id in a result can be passed to
 * get_page / get_links, every label id to search / list_pages.
 */
import { McpServer, type CallToolResult } from "@modelcontextprotocol/server";
import * as z from "zod";
import type { McpSettings } from "./settings";
import { WikiError, type Wiki } from "./wiki";

const READ_ONLY = { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false };

function json(value: object): CallToolResult {
  return { content: [{ type: "text", text: JSON.stringify(value) }] };
}

/** Run a tool body; a WikiError becomes a tool error result the model can read and act on. */
function answer(body: () => CallToolResult): CallToolResult {
  try {
    return body();
  } catch (error) {
    if (error instanceof WikiError) return { isError: true, content: [{ type: "text", text: error.message }] };
    throw error;
  }
}

export function createServer(wiki: Wiki, settings: McpSettings): McpServer {
  const { name, title, version, instructions } = settings.server;
  const server = new McpServer({ name, title, version }, { instructions });
  const pageId = z.string().min(1).describe("Page id (from search, list_pages or a [[page_id|text]] link); a title, alias or page url also works.");
  const label = z.string().min(1).optional().describe("Only pages with this label id (see list_labels), e.g. a country.");
  const section = z.string().min(1).optional().describe("Only pages under this section id (see list_pages tree or a page's section path).");

  server.registerTool(
    "search",
    {
      title: "Search the wiki",
      description:
        "Full-text search over every page (Hebrew or Latin). All words must match; put an exact phrase in double quotes. " +
        "Hebrew prefixes are handled both ways (קולומביה also finds בקולומביה, ומקולומביה; בקולומביה also finds קולומביה); " +
        "niqqud, final letters and Latin case are ignored; English aliases such as Colombia or USA find the Hebrew pages. " +
        "Word endings are not stemmed: add * for any ending (פונדקאי* finds פונדקאית, פונדקאיות). " +
        "Ranked by where words match: title > aliases > entities > questions, summary > description, labels > body. " +
        "Returns total and hits [{id, title, url, fields, snippet}] with matches in **bold**; continue with get_page(id).",
      inputSchema: z.object({
        query: z.string().min(1).describe('Words and/or "exact phrases", e.g. סוכנות "ניתוק זיקה" or Colombia'),
        label,
        section,
        limit: z
          .number()
          .int()
          .min(1)
          .max(settings.search.max_limit)
          .optional()
          .describe(`Maximum hits (default ${settings.search.default_limit}).`),
      }),
      annotations: { title: "Search the wiki", ...READ_ONLY },
    },
    ({ query, label: labelId, section: sectionId, limit }) =>
      answer(() => json(wiki.search(query, { label: labelId, section: sectionId }, limit))),
  );

  server.registerTool(
    "get_page",
    {
      title: "Read a page",
      description:
        "One page: first block is JSON metadata hidden from website readers (url, description, summary, questions it answers, " +
        "section path, labels, aliases, entities, related pages, links_to / linked_from, claims {total, supporting, opposing, " +
        "first_claim_at, last_claim_at} = how many community members backed or disputed the page's claims and when, updated_at); " +
        "second block is the page body in markdown, where [[page_id|text]] links to another page. The body opens with the site's " +
        "disclaimer (Hebrew): the content is AI-generated from group conversations, unverified and not professional advice; " +
        "pass that warning on to your user whenever you use the page.",
      inputSchema: z.object({ id: pageId }),
      annotations: { title: "Read a page", ...READ_ONLY },
    },
    ({ id }) =>
      answer(() => {
        const page = wiki.page(id);
        const result = json(wiki.metadata(page));
        result.content.push({ type: "text", text: wiki.body(page) });
        return result;
      }),
  );

  server.registerTool(
    "list_pages",
    {
      title: "Browse the table of contents",
      description:
        "The wiki's pages with id, title and one-line description, optionally filtered by label or section. " +
        "view=tree (default) nests pages in their sections [{section, title, items}] in site order; view=flat is a plain list.",
      inputSchema: z.object({
        view: z.enum(["tree", "flat"]).optional().describe("tree (default) or flat"),
        label,
        section,
      }),
      annotations: { title: "Browse the table of contents", ...READ_ONLY },
    },
    ({ view, label: labelId, section: sectionId }) =>
      answer(() => json(wiki.listPages(view ?? "tree", { label: labelId, section: sectionId }))),
  );

  server.registerTool(
    "get_links",
    {
      title: "Walk the link graph",
      description:
        "Every page one step from a page, to walk the wiki as a graph. Each neighbour has id, title, description and its relations: " +
        "links_to (this page links to it), linked_from (it links to this page), same_section (sibling in the table of contents), " +
        "or a curated relation from the page's related list (e.g. see_also). Also returns the page's section path.",
      inputSchema: z.object({ id: pageId }),
      annotations: { title: "Walk the link graph", ...READ_ONLY },
    },
    ({ id }) => answer(() => json(wiki.neighbours(wiki.page(id)))),
  );

  server.registerTool(
    "list_labels",
    {
      title: "List labels",
      description:
        "Labels are facets that cut across the table of contents (kind=country, topic, ...). Returns [{id, title, kind, " +
        "description, pages}] where pages is how many pages carry the label; use the id as label= in search or list_pages.",
      inputSchema: z.object({
        kind: z.string().min(1).optional().describe("Only labels of this kind, e.g. country"),
      }),
      annotations: { title: "List labels", ...READ_ONLY },
    },
    ({ kind }) => answer(() => json(wiki.listLabels(kind))),
  );

  return server;
}
