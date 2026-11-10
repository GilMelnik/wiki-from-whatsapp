/**
 * Shape of `wiki_index.json` (contract C11), mirrored by `worker/wiki_index.schema.json`.
 * The site generator (step 9) writes it next to the built pages; the Worker only reads it.
 */

export interface NavNode {
  id: string;
  title: string;
}

export interface Aliases {
  he: string[];
  en: string[];
}

export interface Related {
  id: string;
  relation: string;
}

export interface ClaimStats {
  total: number;
  supporting: number;
  opposing: number;
  first_claim_at: string | null;
  last_claim_at: string | null;
}

export interface IndexPage {
  id: string;
  url: string;
  title: string;
  description: string;
  summary?: string | null;
  questions: string[];
  labels: string[];
  path: NavNode[];
  aliases: Aliases;
  entities: string[];
  related: Related[];
  out_links: string[];
  claims: ClaimStats;
  updated_at: string;
  status?: "draft" | "deprecated";
  text: string;
}

export interface IndexLabel {
  title: string;
  description: string;
  kind: string;
}

export interface WikiIndex {
  index_version: number;
  generated_at: string;
  labels: Record<string, IndexLabel>;
  pages: IndexPage[];
}

/** The part of the Workers environment this script uses: the static-assets binding. */
export interface AssetsBinding {
  fetch(input: Request | URL | string): Promise<Response>;
}

export interface Env {
  ASSETS: AssetsBinding;
}
