import { describe, expect, it } from "vitest";
import { HebrewNormalizer } from "../src/normalize";
import { mcpSettings } from "../src/settings";

const n = new HebrewNormalizer(mcpSettings.search.hebrew_prefixes, mcpSettings.search.min_stem_length);

describe("normalize", () => {
  it("drops niqqud and cantillation", () => {
    expect(n.normalize("תְּרוּמַת בֵּיצִית")).toBe("תרומת ביצית");
    expect(n.normalize("מֵי שָׁפִיר")).toBe("מי שפיר");
  });

  it("decomposes Hebrew presentation forms", () => {
    expect(n.normalize("שּׁוֹ")).toBe("שו");
  });

  it("folds final letters", () => {
    expect(n.normalize("ךםןףץ")).toBe("כמנפצ");
    expect(n.tokens("חוזים")).toEqual(n.tokens("חוזימ"));
  });

  it("removes geresh and gershayim inside a word, in any quote style", () => {
    const expected = ["ארהב"];
    expect(n.tokens('ארה"ב')).toEqual(expected);
    expect(n.tokens("ארה״ב")).toEqual(expected);
    expect(n.tokens("ארה”ב")).toEqual(expected);
    expect(n.tokens("צ'ק")).toEqual(["צק"]);
  });

  it("folds Latin case and accents", () => {
    expect(n.tokens("Clínica ÁNDES México")).toEqual(["clinica", "andes", "mexico"]);
  });

  it("splits Hebrew from Latin and drops detached one-letter Hebrew prefixes", () => {
    expect(n.tokens("ה-IVF")).toEqual(["ivf"]);
    expect(n.tokens("בIVF")).toEqual(["ivf"]);
    expect(n.tokens("ו ב 2015")).toEqual(["2015"]);
  });
});

describe("stems", () => {
  it("strips one-, two- and three-letter prefixes", () => {
    expect(n.stems("בקולומביה")).toContain("קולומביה");
    expect(n.stems("שבקולומביה")).toEqual(expect.arrayContaining(["בקולומביה", "קולומביה"]));
    expect(n.stems(n.tokens("ובמדיין")[0] ?? "")).toContain("מדיינ");
    expect(n.stems("ומהבית")).toEqual(expect.arrayContaining(["מהבית", "הבית", "בית"]));
  });

  it("keeps at least min_stem_length letters", () => {
    expect(n.stems("הבית")).toEqual(["בית"]);
    expect(n.stems("בית")).toEqual([]);
    expect(n.stems("בשר")).toEqual([]);
    expect(n.stems("שר")).toEqual([]);
  });

  it("never strips Latin tokens", () => {
    expect(n.stems("bogota")).toEqual([]);
  });
});

describe("rawWords", () => {
  it("keeps offsets into the original text, niqqud included", () => {
    const text = "בדיקת מֵי שָׁפִיר, ארה״ב";
    const words = [...n.rawWords(text)];
    expect(words.map((w) => text.slice(w.start, w.end))).toEqual(["בדיקת", "מֵי", "שָׁפִיר", "ארה״ב"]);
    expect(words[3]?.tokens).toEqual(["ארהב"]);
  });
});
