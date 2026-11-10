/**
 * The inverted index as two parallel arrays: sorted `terms` and, per term, its postings encoded
 * as "doc,field,tf;..." (tf < 0 marks a text_prefix posting, i.e. the term is the word without
 * a Hebrew prefix). Lookups are binary searches, so a `*` prefix is one contiguous range.
 * The same arrays are the deploy-time snapshot (`wiki_search.json`): arrays of strings parse far
 * faster than an object with one key per term, which keeps cold isolates inside the CPU budget.
 */
import type { HebrewNormalizer } from "./normalize";
import type { SearchField } from "./settings";

export const FIELDS: SearchField[] = ["title", "aliases", "entities", "questions", "summary", "description", "labels", "body"];

export interface Posting {
  doc: number;
  field: SearchField;
  tf: number;
  kind: "exact" | "text_prefix";
}

/** wiki_search.json: the postings arrays plus every page's body tokens (space-joined). */
export interface SearchSnapshot {
  search_version: number;
  index_generated_at: string;
  fields: SearchField[];
  terms: string[];
  postings: string[];
  body_tokens: string[];
}

/** Whether `snapshot` was built from this index (same generated_at) by this code version. */
export function snapshotMatches(snapshot: SearchSnapshot, version: number, generatedAt: string, pages: number): boolean {
  return (
    snapshot.search_version === version &&
    snapshot.index_generated_at === generatedAt &&
    snapshot.fields?.join() === FIELDS.join() &&
    snapshot.terms?.length === snapshot.postings?.length &&
    snapshot.body_tokens?.length === pages
  );
}

type Counts = Map<string, Map<number, { exact: number; prefix: number }>>;

export class Postings {
  private readonly terms: string[];
  private readonly encoded: string[];

  constructor(terms: string[], encoded: string[]) {
    this.terms = terms;
    this.encoded = encoded;
  }

  /** Index the normalised tokens of every field of every document. */
  static build(docTokens: Record<SearchField, string[]>[], normalizer: HebrewNormalizer): Postings {
    const counts: Counts = new Map();
    const stemMemo = new Map<string, string[]>();
    const slot = (key: string, cell: number) => {
      let row = counts.get(key);
      if (!row) {
        row = new Map();
        counts.set(key, row);
      }
      let entry = row.get(cell);
      if (!entry) {
        entry = { exact: 0, prefix: 0 };
        row.set(cell, entry);
      }
      return entry;
    };
    docTokens.forEach((fields, doc) => {
      FIELDS.forEach((field, f) => {
        const cell = doc * FIELDS.length + f;
        const exact = new Map<string, number>();
        for (const token of fields[field]) exact.set(token, (exact.get(token) ?? 0) + 1);
        for (const [token, count] of exact) {
          slot(token, cell).exact += count;
          let stems = stemMemo.get(token);
          if (!stems) {
            stems = normalizer.stems(token);
            stemMemo.set(token, stems);
          }
          for (const stem of stems) slot(stem, cell).prefix += count;
        }
      });
    });
    const terms = [...counts.keys()].sort();
    const encoded = terms.map((term) => {
      const parts: string[] = [];
      (counts.get(term) ?? new Map()).forEach((c, cell) => {
        const head = `${Math.floor(cell / FIELDS.length)},${cell % FIELDS.length}`;
        if (c.exact) parts.push(`${head},${c.exact}`);
        if (c.prefix) parts.push(`${head},${-c.prefix}`);
      });
      return parts.join(";");
    });
    return new Postings(terms, encoded);
  }

  toArrays(): { terms: string[]; postings: string[] } {
    return { terms: this.terms, postings: this.encoded };
  }

  get size(): number {
    return this.terms.length;
  }

  /** Index of the first term >= key. */
  private lowerBound(key: string): number {
    let lo = 0;
    let hi = this.terms.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if ((this.terms[mid] as string) < key) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  }

  private decode(i: number): Posting[] {
    return (this.encoded[i] ?? "").split(";").map((part) => {
      const [doc, field, tf] = part.split(",").map(Number) as [number, number, number];
      return { doc, field: FIELDS[field] as SearchField, tf: Math.abs(tf), kind: tf > 0 ? "exact" : "text_prefix" };
    });
  }

  get(term: string): Posting[] {
    const i = this.lowerBound(term);
    return this.terms[i] === term ? this.decode(i) : [];
  }

  /** Postings of every term that starts with `prefix`. */
  withPrefix(prefix: string): Posting[] {
    const out: Posting[] = [];
    for (let i = this.lowerBound(prefix); this.terms[i]?.startsWith(prefix); i++) out.push(...this.decode(i));
    return out;
  }
}
