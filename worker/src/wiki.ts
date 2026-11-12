/**
 * The loaded wiki: page lookup by id / alias / url, the link graph (in-links are derived from
 * out_links), the table of contents and the label facets. Every method returns plain objects
 * that the MCP tools serialise as JSON; ids in them are valid input for get_page / get_links.
 */
import { HebrewNormalizer } from "./normalize";
import { snapshotMatches, type SearchSnapshot } from "./postings";
import { SearchIndex, matchesFilters, type SearchFilters } from "./search";
import type { McpSettings } from "./settings";
import type { AssetsBinding, IndexPage, WikiIndex } from "./types";

/** An error the model can act on (unknown id, bad filter); tools return it as a tool error. */
export class WikiError extends Error {}

export interface PageRef {
  id: string;
  title: string;
}

export interface PageCard extends PageRef {
  description: string;
}

interface SectionNode {
  section: string;
  title: string;
  items: TreeItem[];
}

type TreeItem = SectionNode | PageCard;

const SUGGESTIONS = 3;
const RELATION_ORDER = ["links_to", "linked_from", "same_section"];

export class Wiki {
  readonly index: WikiIndex;
  readonly searchIndex: SearchIndex;
  private readonly normalizer: HebrewNormalizer;
  private readonly settings: McpSettings;
  private readonly siteUrl: string;
  private readonly byId = new Map<string, IndexPage>();
  private readonly byName = new Map<string, string>();
  private readonly byUrl = new Map<string, string>();
  private readonly inLinks = new Map<string, string[]>();
  private readonly sections = new Map<string, string>();

  /** `snapshot` (wiki_search.json) is used when it was built from this index; else the pages are indexed now. */
  constructor(index: WikiIndex, settings: McpSettings, siteUrl: string, snapshot?: SearchSnapshot) {
    this.index = index;
    this.settings = settings;
    this.siteUrl = siteUrl;
    this.normalizer = new HebrewNormalizer(settings.search.hebrew_prefixes, settings.search.min_stem_length);
    for (const page of index.pages) {
      this.byId.set(page.id, page);
      this.byUrl.set(this.urlKey(page.url), page.id);
      for (const name of [page.title, ...page.aliases.he, ...page.aliases.en]) {
        const key = this.nameKey(name);
        if (key && !this.byName.has(key)) this.byName.set(key, page.id);
      }
      for (const node of page.path) this.sections.set(node.id, node.title);
    }
    for (const page of index.pages) {
      for (const target of new Set(page.out_links)) {
        if (target !== page.id && this.byId.has(target)) {
          this.inLinks.set(target, [...(this.inLinks.get(target) ?? []), page.id]);
        }
      }
    }
    const usable = snapshot && snapshotMatches(snapshot, settings.search_version, index.generated_at, index.pages.length);
    if (snapshot && !usable) console.warn(`${settings.search_file} does not match ${settings.index_file}; indexing in memory.`);
    this.searchIndex = new SearchIndex(index.pages, index.labels, this.normalizer, settings.search, usable ? snapshot : undefined);
  }

  /** Fetch `index_file` (required) and `search_file` (optional) through the assets binding. */
  static async load(assets: AssetsBinding, settings: McpSettings, siteUrl: string): Promise<Wiki> {
    const url = new URL(settings.index_file, `${settings.assets_origin}/`);
    const searchUrl = new URL(settings.search_file, `${settings.assets_origin}/`);
    const [response, searchResponse] = await Promise.all([assets.fetch(url), assets.fetch(searchUrl)]);
    if (!response.ok) {
      throw new Error(`Wiki index ${url.pathname} is not in the static assets (HTTP ${response.status}).`);
    }
    const index = (await response.json()) as WikiIndex;
    if (index.index_version !== settings.index_version || !Array.isArray(index.pages)) {
      throw new Error(
        `Wiki index ${url.pathname} has index_version ${String(index.index_version)}, ` +
          `this server reads version ${settings.index_version}.`,
      );
    }
    if (typeof index.disclaimer !== "string" || !index.disclaimer) {
      throw new Error(`Wiki index ${url.pathname} has no disclaimer; run step 9 again to write it.`);
    }
    if (!searchResponse.ok) console.warn(`${searchUrl.pathname} missing (HTTP ${searchResponse.status}); indexing in memory.`);
    const snapshot = searchResponse.ok ? ((await searchResponse.json()) as SearchSnapshot) : undefined;
    return new Wiki(index, settings, siteUrl, snapshot);
  }

  private nameKey(name: string): string {
    return this.normalizer.tokens(name).join(" ");
  }

  private urlKey(url: string): string {
    const path = new URL(url, this.siteUrl).pathname.replace(/^\/+|\/+$/g, "");
    return decodeURIComponent(path);
  }

  absoluteUrl(page: IndexPage): string {
    return new URL(page.url, this.siteUrl).href;
  }

  private ref(id: string): PageRef | undefined {
    const page = this.byId.get(id);
    return page && { id: page.id, title: page.title };
  }

  private card(page: IndexPage): PageCard {
    return { id: page.id, title: page.title, description: page.description };
  }

  /** Resolve a page id, title, alias or url; unknown input raises a WikiError with suggestions. */
  page(reference: string): IndexPage {
    const ref = reference.trim();
    const id = this.byId.has(ref) ? ref : (this.byName.get(this.nameKey(ref)) ?? this.byUrl.get(this.urlKey(ref)));
    const page = id === undefined ? undefined : this.byId.get(id);
    if (page) return page;
    let suggestions: string[] = [];
    try {
      suggestions = this.searchIndex.search(ref, {}, SUGGESTIONS).hits.map((h) => `${h.id} (${h.title})`);
    } catch {
      suggestions = [];
    }
    const hint = suggestions.length ? ` Closest pages: ${suggestions.join(", ")}.` : " Use search or list_pages.";
    throw new WikiError(`No page with id, title, alias or url ${JSON.stringify(ref)}.${hint}`);
  }

  private checkFilters(filters: SearchFilters): void {
    if (filters.label && !(filters.label in this.index.labels)) {
      const known = Object.keys(this.index.labels).join(", ");
      throw new WikiError(`Unknown label ${JSON.stringify(filters.label)}. Known labels: ${known}.`);
    }
    if (filters.section && !this.sections.has(filters.section) && !this.byId.has(filters.section)) {
      const known = [...this.sections.keys()].join(", ");
      throw new WikiError(`Unknown section ${JSON.stringify(filters.section)}. Known sections: ${known}.`);
    }
  }

  search(query: string, filters: SearchFilters, limit?: number): object {
    this.checkFilters(filters);
    const { default_limit, max_limit } = this.settings.search;
    const capped = Math.min(Math.max(1, limit ?? default_limit), max_limit);
    let result;
    try {
      result = this.searchIndex.search(query, filters, capped);
    } catch (error) {
      throw new WikiError(error instanceof Error ? error.message : String(error));
    }
    const hits = result.hits.map((hit) => ({ ...hit, url: new URL(hit.url, this.siteUrl).href }));
    const hint = result.total ? undefined : "No page contains every word. Try fewer words, another spelling, or a * suffix.";
    return { query, total: result.total, hits, ...(hint && { hint }) };
  }

  /** The page body as get_page returns it: the site's disclaimer line first, as on the markdown twins. */
  body(page: IndexPage): string {
    return `> ${this.index.disclaimer}\n\n${page.text}`;
  }

  /** Metadata of a page (everything but the body), with titles for every referenced id. */
  metadata(page: IndexPage): object {
    const refs = (ids: string[]): PageRef[] => ids.flatMap((id) => this.ref(id) ?? []);
    return {
      id: page.id,
      title: page.title,
      url: this.absoluteUrl(page),
      description: page.description,
      ...(page.summary && { summary: page.summary }),
      questions: page.questions,
      section: page.path,
      labels: page.labels.map((id) => ({ id, ...this.index.labels[id] })),
      aliases: page.aliases,
      entities: page.entities,
      related: page.related.flatMap((r) => {
        const target = this.ref(r.id);
        return target ? [{ ...target, relation: r.relation }] : [];
      }),
      links_to: refs([...new Set(page.out_links)].filter((id) => id !== page.id)),
      linked_from: refs(this.inLinks.get(page.id) ?? []),
      claims: page.claims,
      updated_at: page.updated_at,
      ...(page.status && { status: page.status }),
    };
  }

  /** All pages one step away from `page`, each with every relation it has to it. */
  neighbours(page: IndexPage): object {
    const found = new Map<string, Set<string>>();
    const add = (id: string, relation: string): void => {
      if (id === page.id || !this.byId.has(id)) return;
      found.set(id, (found.get(id) ?? new Set()).add(relation));
    };
    page.related.forEach((r) => add(r.id, r.relation));
    page.out_links.forEach((id) => add(id, "links_to"));
    (this.inLinks.get(page.id) ?? []).forEach((id) => add(id, "linked_from"));
    const parent = page.path.at(-1)?.id;
    for (const other of this.index.pages) {
      if (other.path.at(-1)?.id === parent) add(other.id, "same_section");
    }
    const rank = (relations: Set<string>): number =>
      Math.min(...[...relations].map((r) => (RELATION_ORDER.includes(r) ? RELATION_ORDER.indexOf(r) + 1 : 0)));
    const neighbours = [...found]
      .map(([id, relations]) => ({ ...this.card(this.byId.get(id) as IndexPage), relations: [...relations] }))
      .sort((a, b) => rank(new Set(a.relations)) - rank(new Set(b.relations)));
    return { id: page.id, title: page.title, section: page.path, neighbours };
  }

  listPages(view: "tree" | "flat", filters: SearchFilters): object {
    this.checkFilters(filters);
    const pages = this.index.pages.filter((page) => matchesFilters(page, filters));
    const base = { generated_at: this.index.generated_at, count: pages.length };
    if (view === "flat") return { ...base, pages: pages.map((page) => this.card(page)) };
    const root: TreeItem[] = [];
    const nodes = new Map<string, SectionNode>();
    for (const page of pages) {
      let items = root;
      for (const step of page.path) {
        let node = nodes.get(step.id);
        if (!node) {
          node = { section: step.id, title: step.title, items: [] };
          nodes.set(step.id, node);
          items.push(node);
        }
        items = node.items;
      }
      items.push(this.card(page));
    }
    return { ...base, tree: root };
  }

  listLabels(kind?: string): object {
    const counts = new Map<string, number>();
    for (const page of this.index.pages) for (const id of page.labels) counts.set(id, (counts.get(id) ?? 0) + 1);
    const all = Object.entries(this.index.labels).map(([id, label]) => ({ id, ...label, pages: counts.get(id) ?? 0 }));
    const labels = all.filter((label) => !kind || label.kind === kind);
    if (kind && !labels.length) {
      const kinds = [...new Set(all.map((label) => label.kind))].join(", ");
      throw new WikiError(`No labels of kind ${JSON.stringify(kind)}. Kinds: ${kinds}.`);
    }
    labels.sort((a, b) => a.kind.localeCompare(b.kind) || b.pages - a.pages || a.title.localeCompare(b.title, "he"));
    return { labels };
  }
}
