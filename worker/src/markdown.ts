/**
 * Plain text of a page body for indexing and snippets. Wiki links ([[page_id|text]],
 * [[label:id|text]]) keep their display text (or the id), markdown links keep their text,
 * footnote references ([^g-1a2b]) and URLs disappear, admonition headers keep their title.
 * Not a markdown renderer: punctuation left behind is ignored by the tokenizer.
 */

const WIKI_LINK = /\[\[(?:label:)?([\w\-./]+)(?:\|((?:[^\]\n]|\](?!\]))*))?\]\]/g;
const MD_IMAGE = /!\[([^\]\n]*)\]\([^)\n]*\)/g;
const MD_LINK = /\[([^\]\n]+)\]\([^)\n]*\)/g;
const FOOTNOTE_REF = /\[\^[^\]\n]+\](?!:)/g;
const FOOTNOTE_DEF = /^\[\^[^\]\n]+\]:\s*/gm;
const ADMONITION = /^\s*(?:!!!|\?\?\?\+?)\s*\w+(?:\s+"([^"\n]*)")?\s*$/gm;
const URL = /https?:\/\/\S+/g;
const MARKUP = /[#*_`>|~]+/g;
const SPACES = /[ \t]+/g;

export function toPlainText(markdown: string): string {
  return markdown
    .replace(WIKI_LINK, (_m, id: string, text: string | undefined) => text || id)
    .replace(MD_IMAGE, "$1")
    .replace(MD_LINK, "$1")
    .replace(FOOTNOTE_DEF, "")
    .replace(FOOTNOTE_REF, "")
    .replace(ADMONITION, (_m, title: string | undefined) => title ?? "")
    .replace(URL, "")
    .replace(MARKUP, " ")
    .replace(SPACES, " ");
}
