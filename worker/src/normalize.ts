/**
 * Text normalisation and tokenisation for Hebrew + Latin search, without any model.
 *
 * normalize: NFKD, drop every combining mark (Hebrew niqqud and cantillation, Latin accents),
 * drop a geresh / gershayim between two Hebrew letters (ארה"ב -> ארהב, צ'ק -> צק), lower-case,
 * fold the five Hebrew final letters to their regular form (ם -> מ ...).
 * tokens: maximal runs of Hebrew letters, or of other letters / digits (so "ב-IVF" and "בIVF"
 * both give "ivf"); one-letter Hebrew tokens are dropped (they are detached prefixes / particles).
 * stems: the forms of a Hebrew token without a leading one-letter prefix (ו ה ב ל מ ש כ) or a
 * listed combination (וב, של, ומה ...), keeping at least `minStemLength` letters.
 */

const HEBREW_RUN = "\\u05D0-\\u05EA";
const MARKS = /\p{M}/gu;
const GERSHAYIM = new RegExp(`(?<=[${HEBREW_RUN}])["'\\u05F3\\u05F4\\u2019\\u201D](?=[${HEBREW_RUN}])`, "g");
const TOKEN = new RegExp(`[${HEBREW_RUN}]+|(?:(?![${HEBREW_RUN}])[\\p{L}\\p{N}])+`, "gu");
const HEBREW_TOKEN = new RegExp(`^[${HEBREW_RUN}]+$`);
const HAS_HEBREW = new RegExp(`[${HEBREW_RUN}]`);
const SEPARATORS = /[^\p{L}\p{N}]+/u;
const PLAIN_ASCII = /^[A-Za-z0-9]+$/;
/** A word of the original text, possibly with niqqud and an inner geresh, for snippet offsets. */
const RAW_WORD = /[\p{L}\p{N}\p{M}]+(?:["'׳״’”][\p{L}\p{N}\p{M}]+)*/gu;

export interface RawWord {
  start: number;
  end: number;
  tokens: string[];
}

export class HebrewNormalizer {
  private readonly prefixes: string[];
  private readonly minStemLength: number;

  constructor(prefixes: string[], minStemLength: number) {
    this.prefixes = [...new Set(prefixes.map((p) => this.normalize(p)))];
    this.minStemLength = minStemLength;
  }

  /** Strip marks only (no case or final-letter folding): used before parsing query quotes. */
  stripMarks(text: string): string {
    return text.normalize("NFKD").replace(MARKS, "").replace(GERSHAYIM, "");
  }

  private foldFinals(text: string): string {
    return text
      .replaceAll("ך", "כ")
      .replaceAll("ם", "מ")
      .replaceAll("ן", "נ")
      .replaceAll("ף", "פ")
      .replaceAll("ץ", "צ");
  }

  normalize(text: string): string {
    return this.foldFinals(this.stripMarks(text).toLowerCase());
  }

  tokens(text: string): string[] {
    const out: string[] = [];
    for (const piece of this.normalize(text).split(SEPARATORS)) {
      if (HEBREW_TOKEN.test(piece)) {
        if (piece.length > 1) out.push(piece);
      } else if (HAS_HEBREW.test(piece)) {
        for (const match of piece.matchAll(TOKEN)) {
          if (match[0].length > 1 || !HEBREW_TOKEN.test(match[0])) out.push(match[0]);
        }
      } else if (piece) {
        out.push(piece);
      }
    }
    return out;
  }

  /** tokens() of one word, with a fast path for plain Hebrew or ASCII words. */
  private wordTokens(word: string): string[] {
    if (HEBREW_TOKEN.test(word)) return word.length > 1 ? [this.foldFinals(word)] : [];
    if (PLAIN_ASCII.test(word)) return [word.toLowerCase()];
    return this.tokens(word);
  }

  isHebrew(token: string): boolean {
    return HEBREW_TOKEN.test(token);
  }

  /** Prefix-stripped forms of a normalised token (empty for Latin and short tokens). */
  stems(token: string): string[] {
    const stems: string[] = [];
    if (this.isHebrew(token)) {
      for (const prefix of this.prefixes) {
        if (token.length - prefix.length >= this.minStemLength && token.startsWith(prefix)) {
          stems.push(token.slice(prefix.length));
        }
      }
    }
    return stems;
  }

  /** Words of the original text with their offsets and normalised tokens, lazily. */
  *rawWords(text: string): Generator<RawWord> {
    for (const match of text.matchAll(RAW_WORD)) {
      const tokens = this.wordTokens(match[0]);
      if (tokens.length) yield { start: match.index, end: match.index + match[0].length, tokens };
    }
  }
}
