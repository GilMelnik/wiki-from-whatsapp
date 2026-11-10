import { describe, expect, it } from "vitest";
import type { SearchFilters } from "../src/search";
import { makeWiki } from "./helpers";

const wiki = makeWiki();
const search = (query: string, filters: SearchFilters = {}, limit = 50) =>
  wiki.searchIndex.search(query, filters, limit);
const ids = (query: string, filters: SearchFilters = {}): string[] => search(query, filters).hits.map((h) => h.id);

describe("Hebrew prefixes", () => {
  it("a bare word finds prefixed forms (ב, ל, ו, שב, ממ)", () => {
    const hits = ids("קולומביה");
    expect(hits[0]).toBe("colombia");
    // mexico: לקולומביה / בקולומביה; costs: וקולומביה; agencies: שבקולומביה
    expect(hits).toEqual(expect.arrayContaining(["mexico", "costs", "agencies", "egg-donation", "parental-order"]));
    expect(ids("מקסיקו")).toEqual(expect.arrayContaining(["mexico", "colombia", "costs"]));
  });

  it("a prefixed query finds the bare form, ranked below exact matches", () => {
    const hits = ids("בקולומביה");
    expect(hits).toEqual(expect.arrayContaining(["colombia", "egg-donation", "parental-order"]));
    expect(hits.indexOf("colombia")).toBeLessThan(hits.indexOf("egg-donation"));
  });

  it("two-letter prefix plus a final letter: מדיין finds ובמדיין", () => {
    expect(ids("מדיין")).toEqual(["colombia"]);
  });

  it("words shorter than min_stem_length are never prefix-expanded", () => {
    expect(ids("שר")).toEqual(["parental-order"]); // not books (בשר)
    expect(ids("בשר")).toEqual(["books"]); // not parental-order (שר)
  });

  it("known trade-off: ספר also matches מספר (מ+ספר), ranked below the exact word", () => {
    const hits = ids("ספר");
    expect(hits[0]).toBe("books");
    expect(hits).toContain("costs");
  });

  it("no suffix matching without *", () => {
    expect(ids("קולומביאני")).toEqual([]);
    expect(ids("ביציות")).toEqual(["egg-donation"]); // title only, not ביצית
    expect(ids("ספריות")).toEqual(["books"]); // בספריות = ב + ספריות
    expect(ids("ספרייה")).toEqual([]);
  });
});

describe("niqqud, finals, gershayim, Latin", () => {
  it("text with niqqud matches a plain query and vice versa", () => {
    expect(ids('"תרומת ביצית"')).toEqual(["egg-donation"]);
    expect(ids("בֵּיצִית")).toEqual(["egg-donation"]);
    expect(ids('"מי שפיר"')).toEqual(["colombia"]);
  });

  it("gershayim in any quote style, and the alias written out", () => {
    for (const query of ['ארה"ב', "ארה״ב", "ארהב"]) expect(ids(query)[0]).toBe("usa");
    expect(ids('"ארצות הברית"')[0]).toBe("usa");
  });

  it("Latin aliases and entities find Hebrew pages, case- and accent-insensitive", () => {
    expect(ids("Colombia")[0]).toBe("colombia");
    expect(ids("USA")[0]).toBe("usa");
    expect(ids('"united states"')).toEqual(["usa"]);
    expect(ids("mexico")[0]).toBe("mexico");
    expect(ids("clinica andes")).toEqual(expect.arrayContaining(["colombia", "agencies"]));
    expect(ids("IVF")).toEqual(["usa"]);
  });

  it("wiki-link ids, URLs and footnote ids are not indexed", () => {
    expect(ids("parental")).toEqual(["parental-order"]); // alias, not the [[parental-order|...]] in colombia
    expect(ids("1a2b3c4d")).toEqual([]);
  });
});

describe("wildcards, phrases, AND", () => {
  it("* matches any ending, also behind a prefix and with a final letter typed", () => {
    expect(ids("סכום*")).toEqual(expect.arrayContaining(["usa", "costs"]));
    expect(ids("סוכנו*")).toEqual(expect.arrayContaining(["agencies", "colombia", "usa", "costs"]));
    expect(ids("ספרי*")).toEqual(expect.arrayContaining(["books"]));
  });

  it("phrases must be consecutive", () => {
    expect(ids('"צו הורות"')[0]).toBe("parental-order");
    expect(ids('"הורות צו"')).toEqual([]);
  });

  it("every word must match", () => {
    const hits = ids("קולומביה מקסיקו");
    expect(hits).toEqual(expect.arrayContaining(["colombia", "mexico", "costs"]));
    expect(hits).not.toContain("usa");
    expect(hits).not.toContain("books");
  });

  it("an unbalanced quote is read as plain words; a quote between Hebrew letters is gershayim", () => {
    expect(ids('"צו הורות')).toEqual(ids("צו הורות"));
    expect(ids('ארה"ב "בית משפט"')).toEqual(["usa"]);
  });

  it("a query with no word is an error", () => {
    expect(() => search("?! -")).toThrow(/no searchable words/);
  });
});

describe("ranking, filters, snippets", () => {
  it("title outranks body", () => {
    expect(ids("עלויות")[0]).toBe("costs");
    expect(ids("סוכנות")[0]).toBe("agencies");
  });

  it("filters by label and by section", () => {
    expect(ids("פונדקאות", { label: "colombia" })).toEqual(["colombia"]);
    expect(ids("פונדקאות", { section: "latin-america" }).sort()).toEqual(["colombia", "mexico"]);
    expect(ids("פונדקאות", { section: "destinations" })).toHaveLength(3);
  });

  it("reports matched fields by weight and a bold snippet from the best prose field", () => {
    const hit = search("בוגוטה").hits[0];
    expect(hit?.id).toBe("colombia");
    expect(hit?.fields).toEqual(["entities", "body"]);
    expect(hit?.snippet).toContain("**בבוגוטה**");
  });

  it("keeps snippets short and marks cuts with an ellipsis", () => {
    const snippet = search("ציבוריים").hits[0]?.snippet ?? "";
    expect(snippet.startsWith("…")).toBe(true);
    expect(snippet.replaceAll("**", "").length).toBeLessThanOrEqual(202);
    expect(snippet).toContain("**ציבוריים**");
  });

  it("highlights a niqqud word in its original spelling", () => {
    expect(search("ביצית").hits[0]?.snippet).toContain("**בֵּיצִית**");
  });

  it("falls back to the description when only the title matched", () => {
    expect(search("ומימון").hits[0]?.snippet).toBe("כמה עולה תהליך פונדקאות ואיך מממנים אותו");
  });
});

describe("phrase snippets", () => {
  it("takes the snippet from a field that holds the phrase", () => {
    // הורות is also in questions and summary (weight 4), the phrase only in the body (weight 1)
    const hit = search('"הורות לבית"').hits[0];
    expect(hit?.fields).toEqual(expect.arrayContaining(["questions", "body"]));
    expect(hit?.snippet).toContain("**הורות** **לבית**");
  });
});
