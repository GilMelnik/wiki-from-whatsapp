/**
 * Search over the wiki pages: postings (see postings.ts) find candidate pages, page fields are
 * tokenised lazily only for phrase checks and snippets of the pages that need them.
 *
 * Every query word must match (AND); a quoted phrase must also appear as consecutive words in
 * one field. A query word `q` matches a text word `t` when (best first):
 *   exact        t == q
 *   text_prefix  t is q behind a Hebrew prefix            (q=קולומביה, t=בקולומביה)
 *   wildcard     q ends with * and t (or t without prefix) starts with q
 *   query_prefix q without its prefix equals t, or t without its prefix (q=בקולומביה, t=קולומביה)
 * Score = sum over words and fields of field_weight * match_weight * (1 + ln tf),
 * plus field_weight * phrase length for every field that holds a phrase.
 */
import { toPlainText } from "./markdown";
import type { HebrewNormalizer, RawWord } from "./normalize";
import { FIELDS, Postings, type Posting, type SearchSnapshot } from "./postings";
import type { MatchKind, SearchField, SearchSettings } from "./settings";
import type { IndexLabel, IndexPage } from "./types";

const SNIPPET_FIELDS = new Set<SearchField>(["body", "summary", "questions", "description"]);
const LIST_JOIN = " · ";
const ELLIPSIS = "…";
const WHITESPACE = /\s+/g;

interface FieldMatch {
  weight: number;
  tf: number;
}

type TermMatches = Map<number, Map<SearchField, FieldMatch>>;

interface Doc {
  index: number;
  page: IndexPage;
  text: Record<SearchField, string>;
  tokens: Partial<Record<SearchField, string[]>>;
}

export interface QueryTerm {
  text: string;
  wildcard: boolean;
  /** The term without a Hebrew prefix (for query_prefix matches). */
  stems: string[];
  /** Shortest of text and stems: every word a non-wildcard term matches ends with it. */
  tail: string;
}

export interface ParsedQuery {
  terms: QueryTerm[];
  phrases: QueryTerm[][];
}

export interface SearchFilters {
  label?: string | undefined;
  section?: string | undefined;
}

export interface SearchHit {
  id: string;
  title: string;
  url: string;
  fields: SearchField[];
  snippet: string;
}

export interface SearchResult {
  total: number;
  hits: SearchHit[];
}

function termKey(term: QueryTerm): string {
  return `${term.text}${term.wildcard ? "*" : ""}`;
}

/** A page passes when it has the label and lies under the section (a path node id or itself). */
export function matchesFilters(page: IndexPage, filters: SearchFilters): boolean {
  if (filters.label && !page.labels.includes(filters.label)) return false;
  if (filters.section && page.id !== filters.section && !page.path.some((n) => n.id === filters.section)) {
    return false;
  }
  return true;
}

export class SearchIndex {
  readonly postings: Postings;
  private readonly pages: IndexPage[];
  private readonly labels: Record<string, IndexLabel>;
  private readonly docs: (Doc | undefined)[];
  /** Body tokens from the snapshot, split on first use. */
  private readonly bodyTokens: (string | undefined)[];
  private readonly normalizer: HebrewNormalizer;
  private readonly settings: SearchSettings;

  /** Uses a deploy-time snapshot (already checked with snapshotMatches) when given, else indexes now. */
  constructor(
    pages: IndexPage[],
    labels: Record<string, IndexLabel>,
    normalizer: HebrewNormalizer,
    settings: SearchSettings,
    snapshot?: SearchSnapshot,
  ) {
    this.pages = pages;
    this.labels = labels;
    this.normalizer = normalizer;
    this.settings = settings;
    this.docs = new Array<Doc | undefined>(pages.length);
    this.bodyTokens = snapshot?.body_tokens ?? [];
    this.postings = snapshot
      ? new Postings(snapshot.terms, snapshot.postings)
      : Postings.build(pages.map((_, i) => this.allTokens(i)), normalizer);
  }

  /** The deploy-time snapshot of this index (scripts/build-search.ts). */
  snapshot(version: number, generatedAt: string): SearchSnapshot {
    return {
      search_version: version,
      index_generated_at: generatedAt,
      fields: FIELDS,
      ...this.postings.toArrays(),
      body_tokens: this.pages.map((_, i) => this.fieldTokens(this.doc(i), "body").join(" ")),
    };
  }

  private allTokens(index: number): Record<SearchField, string[]> {
    const doc = this.doc(index);
    return Object.fromEntries(FIELDS.map((f) => [f, this.fieldTokens(doc, f)])) as Record<SearchField, string[]>;
  }

  /** Tokens of one field: from the snapshot for the body when shipped, else tokenised now; kept. */
  private fieldTokens(doc: Doc, field: SearchField): string[] {
    const cached = doc.tokens[field];
    if (cached) return cached;
    const shipped = field === "body" ? this.bodyTokens[doc.index] : undefined;
    const tokens = shipped === undefined ? this.normalizer.tokens(doc.text[field]) : shipped.split(" ").filter(Boolean);
    doc.tokens[field] = tokens;
    return tokens;
  }

  /** Plain text of a page's fields, made on first use and kept (tokens are added per field). */
  private doc(index: number): Doc {
    const cached = this.docs[index];
    if (cached) return cached;
    const page = this.pages[index] as IndexPage;
    const text: Record<SearchField, string> = {
      title: page.title,
      aliases: [...page.aliases.he, ...page.aliases.en].join(LIST_JOIN),
      entities: page.entities.join(LIST_JOIN),
      questions: page.questions.join(LIST_JOIN),
      summary: page.summary ?? "",
      description: page.description,
      labels: page.labels.map((id) => this.labels[id]?.title ?? id).join(LIST_JOIN),
      body: toPlainText(page.text),
    };
    const doc = { index, page, text, tokens: {} };
    this.docs[index] = doc;
    return doc;
  }

  /** Split a raw query into words and quoted phrases (a " between Hebrew letters is gershayim). */
  parse(query: string): ParsedQuery {
    const parts = this.normalizer.stripMarks(query).replace(/[“”„]/g, '"').split('"');
    if (parts.length % 2 === 0) parts.splice(-2, 2, `${parts.at(-2)} ${parts.at(-1)}`);
    const terms: QueryTerm[] = [];
    const phrases: QueryTerm[][] = [];
    parts.forEach((part, i) => {
      const words = this.toTerms(part);
      if (i % 2 === 1 && words.length > 1) phrases.push(words);
      terms.push(...words);
    });
    const unique = new Map(terms.map((t) => [termKey(t), t]));
    return { terms: [...unique.values()], phrases };
  }

  private toTerms(segment: string): QueryTerm[] {
    const terms: QueryTerm[] = [];
    for (const word of segment.split(WHITESPACE)) {
      const wildcard = word.endsWith("*");
      const tokens = this.normalizer.tokens(word);
      tokens.forEach((text, i) => {
        const isLast = i === tokens.length - 1;
        const isWildcard = wildcard && isLast && text.length >= this.settings.min_wildcard_length;
        const stems = isWildcard ? [] : this.normalizer.stems(text);
        const tail = stems.reduce((short, stem) => (stem.length < short.length ? stem : short), text);
        terms.push({ text, wildcard: isWildcard, stems, tail });
      });
    }
    return terms;
  }

  /** Match weight of a query term against one normalised text token (0 = no match). */
  private termWeight(term: QueryTerm, token: string): number {
    const weights = this.settings.match_weights;
    if (term.wildcard ? !token.includes(term.text) : !token.endsWith(term.tail)) return 0;
    if (token === term.text) return weights.exact;
    const tokenStems = this.normalizer.stems(token);
    if (term.wildcard) {
      const hit = token.startsWith(term.text) || tokenStems.some((s) => s.startsWith(term.text));
      return hit ? weights.wildcard : 0;
    }
    if (tokenStems.includes(term.text)) return weights.text_prefix;
    const viaQuery = term.stems.includes(token) || term.stems.some((s) => tokenStems.includes(s));
    return viaQuery ? weights.query_prefix : 0;
  }

  private lookup(term: QueryTerm): TermMatches {
    const matches: TermMatches = new Map();
    const add = (postings: Posting[], kind: MatchKind | null): void => {
      for (const p of postings) {
        const weight = this.settings.match_weights[kind ?? p.kind];
        const fields = matches.get(p.doc) ?? new Map<SearchField, FieldMatch>();
        const current = fields.get(p.field);
        if (!current || weight > current.weight) fields.set(p.field, { weight, tf: p.tf });
        else if (weight === current.weight) current.tf += p.tf;
        matches.set(p.doc, fields);
      }
    };
    if (term.wildcard) {
      add(this.postings.withPrefix(term.text), "wildcard");
      return matches;
    }
    add(this.postings.get(term.text), null);
    for (const stem of term.stems) add(this.postings.get(stem), "query_prefix");
    return matches;
  }

  /** Fields of `doc` among `candidates` that hold `phrase` as consecutive words. */
  private phraseFields(doc: Doc, phrase: QueryTerm[], candidates: SearchField[]): SearchField[] {
    return candidates.filter((field) => {
      const tokens = this.fieldTokens(doc, field);
      for (let i = 0; i + phrase.length <= tokens.length; i++) {
        if (phrase.every((term, j) => this.termWeight(term, tokens[i + j] ?? "") > 0)) return true;
      }
      return false;
    });
  }

  search(query: string, filters: SearchFilters, limit: number): SearchResult {
    const parsed = this.parse(query);
    if (!parsed.terms.length) throw new Error(`The query ${JSON.stringify(query)} has no searchable words.`);
    const byKey = new Map(parsed.terms.map((term) => [termKey(term), this.lookup(term)]));
    const perTerm = [...byKey.values()].sort((a, b) => a.size - b.size);
    /** Fields where every word of the phrase matched: the only ones worth tokenising. */
    const phraseCandidates = (doc: number, phrase: QueryTerm[]): SearchField[] =>
      FIELDS.filter((field) => phrase.every((term) => byKey.get(termKey(term))?.get(doc)?.has(field)));
    const weights = this.settings.field_weights;
    const scored: { doc: number; score: number; fields: Set<SearchField>; phraseFields: SearchField[][] }[] = [];
    for (const doc of perTerm[0]?.keys() ?? []) {
      if (!perTerm.every((m) => m.has(doc))) continue;
      const page = this.pages[doc];
      if (!page || !matchesFilters(page, filters)) continue;
      const phraseFields = parsed.phrases.map((phrase) =>
        this.phraseFields(this.doc(doc), phrase, phraseCandidates(doc, phrase)),
      );
      if (phraseFields.some((fields) => !fields.length)) continue;
      let score = 0;
      const fields = new Set<SearchField>();
      for (const matches of perTerm) {
        for (const [field, m] of matches.get(doc) ?? []) {
          score += weights[field] * m.weight * (1 + Math.log(m.tf));
          fields.add(field);
        }
      }
      phraseFields.forEach((list, i) => {
        for (const field of list) score += weights[field] * (parsed.phrases[i]?.length ?? 0);
      });
      scored.push({ doc, score, fields, phraseFields });
    }
    scored.sort((a, b) => b.score - a.score || a.doc - b.doc);
    const hits = scored.slice(0, limit).map(({ doc, fields, phraseFields }) => {
      const entry = this.doc(doc);
      const ordered = [...fields].sort((a, b) => weights[b] - weights[a]);
      const prose = ordered.filter((f) => SNIPPET_FIELDS.has(f));
      const snippetField =
        prose.find((f) => phraseFields.every((list) => list.includes(f))) ?? prose[0] ?? "description";
      return {
        id: entry.page.id,
        title: entry.page.title,
        url: entry.page.url,
        fields: ordered,
        snippet: this.snippet(entry.text[snippetField], parsed.terms),
      };
    });
    return { total: scored.length, hits };
  }

  /** About `snippet_chars` characters around the first match, matched words in **bold**. */
  snippet(text: string, terms: QueryTerm[]): string {
    const size = this.settings.snippet_chars;
    const flat = text.replace(WHITESPACE, " ").trim();
    const matched: RawWord[] = [];
    for (const word of this.normalizer.rawWords(flat)) {
      const first = matched[0];
      if (first && word.start > first.start + size) break;
      if (word.tokens.some((tok) => terms.some((t) => this.termWeight(t, tok) > 0))) matched.push(word);
    }
    const anchor = matched[0]?.start ?? 0;
    let start = Math.max(0, anchor - Math.floor(size / 3));
    if (start > 0) {
      const space = flat.indexOf(" ", start);
      start = space >= 0 && space < anchor ? space + 1 : anchor;
    }
    let end = Math.min(flat.length, start + size);
    if (end < flat.length) {
      const space = flat.lastIndexOf(" ", end);
      if (space > start) end = space;
    }
    let out = "";
    let pos = start;
    for (const word of matched) {
      if (word.start < start || word.end > end) continue;
      out += `${flat.slice(pos, word.start)}**${flat.slice(word.start, word.end)}**`;
      pos = word.end;
    }
    out += flat.slice(pos, end);
    return `${start > 0 ? ELLIPSIS : ""}${out.trim()}${end < flat.length ? ELLIPSIS : ""}`;
  }
}
