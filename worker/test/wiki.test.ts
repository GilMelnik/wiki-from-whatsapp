import { describe, expect, it } from "vitest";
import { mcpSettings, siteUrl } from "../src/settings";
import { Wiki, WikiError } from "../src/wiki";
import { fakeAssets, index, makeWiki, snapshotOf } from "./helpers";

const wiki = makeWiki();
type Obj = Record<string, unknown>;
const meta = (id: string) => wiki.metadata(wiki.page(id)) as Obj & { linked_from: { id: string }[]; links_to: { id: string }[] };

describe("page lookup", () => {
  it("resolves ids, titles, aliases in Hebrew and Latin, and urls", () => {
    for (const ref of ["usa", "ארצות הברית", 'ארה"ב', "ארה״ב", "USA", "united states", `${siteUrl}usa/`, "/usa/", "usa/"]) {
      expect(wiki.page(ref).id).toBe("usa");
    }
    expect(wiki.page("  Colombia ").id).toBe("colombia");
  });

  it("explains an unknown reference with the closest pages", () => {
    expect(() => wiki.page("סוכנות בקולומביה")).toThrow(WikiError);
    expect(() => wiki.page("סוכנות בקולומביה")).toThrow(/Closest pages: .*agencies/);
    expect(() => wiki.page("zzz")).toThrow(/Use search or list_pages/);
  });
});

describe("metadata and link graph", () => {
  it("derives in-links from out_links, for every page", () => {
    for (const page of index.pages) {
      for (const target of page.out_links) {
        expect(meta(target).linked_from.map((r) => r.id)).toContain(page.id);
      }
    }
    expect(meta("colombia").linked_from.map((r) => r.id)).toEqual(["mexico", "agencies", "egg-donation", "parental-order"]);
    expect(meta("books").linked_from).toEqual([]);
  });

  it("returns titled references, absolute urls, label kinds and claims", () => {
    const m = meta("colombia");
    expect(m.url).toBe("https://gaysurrogacy.co.il/colombia/");
    expect(m.links_to).toEqual([
      { id: "costs", title: "עלויות ומימון" },
      { id: "mexico", title: "מקסיקו" },
      { id: "agencies", title: "סוכנויות פונדקאות" },
      { id: "parental-order", title: "צו הורות וניתוק זיקה" },
    ]);
    expect(m.labels).toEqual([{ id: "colombia", title: "קולומביה", description: "עמודים על פונדקאות בקולומביה", kind: "country" }]);
    expect(m.related).toEqual([
      { id: "mexico", title: "מקסיקו", relation: "compare_with" },
      { id: "costs", title: "עלויות ומימון", relation: "see_also" },
    ]);
    expect(m.claims).toEqual(index.pages[0]?.claims);
    expect(m).not.toHaveProperty("status");
    expect(meta("mexico").status).toBe("draft");
    expect(meta("mexico")).not.toHaveProperty("summary");
  });

  it("lists every neighbour once with all its relations, curated relations first", () => {
    const result = wiki.neighbours(wiki.page("colombia")) as { neighbours: { id: string; relations: string[] }[] };
    const byId = Object.fromEntries(result.neighbours.map((n) => [n.id, n.relations]));
    expect(byId.mexico).toEqual(["compare_with", "links_to", "linked_from", "same_section"]);
    expect(byId.costs).toEqual(["see_also", "links_to"]);
    expect(byId["egg-donation"]).toEqual(["linked_from"]);
    expect(result.neighbours[0]?.id).toBe("mexico");
    const books = wiki.neighbours(wiki.page("books")) as { neighbours: { id: string; relations: string[] }[] };
    expect(books.neighbours).toEqual([expect.objectContaining({ id: "parental-order", relations: ["same_section"] })]);
  });
});

describe("table of contents and labels", () => {
  it("nests pages in their sections, in site order", () => {
    const { tree, count } = wiki.listPages("tree", {}) as { tree: Obj[]; count: number };
    expect(count).toBe(8);
    expect(tree.map((n) => n.section)).toEqual(["destinations", "process", "after-birth"]);
    const latam = (tree[0]?.items as Obj[])[0] as Obj;
    expect(latam.section).toBe("latin-america");
    expect((latam.items as Obj[]).map((p) => p.id)).toEqual(["colombia", "mexico"]);
  });

  it("filters the flat list by label or section", () => {
    const flat = (filters: Obj) => (wiki.listPages("flat", filters) as { pages: Obj[] }).pages.map((p) => p.id);
    expect(flat({ label: "legal" })).toEqual(["parental-order"]);
    expect(flat({ section: "process" })).toEqual(["agencies", "costs", "egg-donation"]);
    expect(() => flat({ label: "nope" })).toThrow(/Known labels: colombia/);
    expect(() => flat({ section: "nope" })).toThrow(/Known sections: destinations/);
  });

  it("counts pages per label and filters by kind", () => {
    const { labels } = wiki.listLabels() as { labels: Obj[] };
    expect(labels).toHaveLength(7);
    expect(labels.find((l) => l.id === "israel")).toMatchObject({ kind: "country", pages: 1, title: "ישראל" });
    expect((wiki.listLabels("topic") as { labels: Obj[] }).labels.map((l) => l.id).sort()).toEqual(["legal", "medical", "money"]);
    expect(() => wiki.listLabels("city")).toThrow(/Kinds: country, topic/);
  });
});

describe("Wiki.load", () => {
  it("reads the index through the assets binding", async () => {
    const assets = fakeAssets();
    const loaded = await Wiki.load(assets, mcpSettings, siteUrl);
    expect(loaded.index.pages).toHaveLength(8);
    expect(assets.requests).toEqual([`/${mcpSettings.index_file}`, `/${mcpSettings.search_file}`]);
  });

  it("uses a matching search snapshot and gives the same results as indexing in memory", async () => {
    const snapshot = snapshotOf(index);
    const fromSnapshot = await Wiki.load(fakeAssets(index, snapshot), mcpSettings, siteUrl);
    expect(fromSnapshot.searchIndex.postings.size).toBe(snapshot.terms.length);
    for (const query of ["קולומביה", "בקולומביה", "סוכנו*", '"צו הורות"', "USA", "שר"]) {
      expect(fromSnapshot.search(query, {})).toEqual(wiki.search(query, {}));
    }
  });

  it("ignores a snapshot built from another index and indexes in memory", () => {
    const stale = { ...snapshotOf(index), index_generated_at: "2020-01-01T00:00:00Z", terms: [], postings: [] };
    const rebuilt = new Wiki(structuredClone(index), mcpSettings, siteUrl, stale);
    expect(rebuilt.searchIndex.postings.size).toBeGreaterThan(0);
    expect(rebuilt.search("קולומביה", {})).toEqual(wiki.search("קולומביה", {}));
  });

  it("fails clearly when the index is missing, has another version or carries no disclaimer", async () => {
    await expect(Wiki.load(fakeAssets(null), mcpSettings, siteUrl)).rejects.toThrow(/not in the static assets \(HTTP 404\)/);
    await expect(Wiki.load(fakeAssets({ ...index, index_version: 2 }), mcpSettings, siteUrl)).rejects.toThrow(/index_version 2/);
    const { disclaimer: _disclaimer, ...withoutDisclaimer } = index;
    await expect(Wiki.load(fakeAssets(withoutDisclaimer), mcpSettings, siteUrl)).rejects.toThrow(/has no disclaimer/);
  });
});
