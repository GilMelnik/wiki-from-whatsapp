/** Typed view of the `mcp` and `site` sections of the project's `config.json`. */
import config from "../../config.json";

export type SearchField =
  | "title"
  | "aliases"
  | "entities"
  | "questions"
  | "summary"
  | "description"
  | "labels"
  | "body";

export type MatchKind = "exact" | "text_prefix" | "wildcard" | "query_prefix";

export interface SearchSettings {
  default_limit: number;
  max_limit: number;
  snippet_chars: number;
  min_stem_length: number;
  min_wildcard_length: number;
  hebrew_prefixes: string[];
  field_weights: Record<SearchField, number>;
  match_weights: Record<MatchKind, number>;
}

export interface McpSettings {
  route: string;
  index_file: string;
  index_version: number;
  search_file: string;
  search_version: number;
  assets_origin: string;
  allowed_origin_hostnames: string[];
  server: { name: string; title: string; version: string; instructions: string };
  search: SearchSettings;
}

export const mcpSettings: McpSettings = config.mcp as McpSettings;
export const siteUrl: string = config.site.url;
export const twinFile: string = config.site.agent_markdown.twin_file;
