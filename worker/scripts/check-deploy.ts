/**
 * After-deploy check of the live site (the last step of the deploy workflow): the text files a
 * browser or an agent reads as they are (the home page's markdown twin, llms.txt) must be
 * served as UTF-8, or their Hebrew shows as garbage. A fresh deploy can take a moment to answer,
 * so a file is asked for a few times before it counts as wrong.
 * Usage: tsx scripts/check-deploy.ts [site url]   (default: `site.url` of config.json)
 */
/// <reference types="node" />
import { pathToFileURL } from "node:url";
import config from "../../config.json";

const { url: siteUrl, agent_markdown: files, deploy_check: retry } = config.site;

/** What is wrong with a text file as the host served it; null when it reads as UTF-8 Hebrew. */
export function textFileProblem(status: number, contentType: string | null, body: Uint8Array): string | null {
  if (status !== 200) return `status ${status}`;
  if (!/charset=utf-8/i.test(contentType ?? "")) return `content-type "${contentType}" does not say charset=utf-8`;
  try {
    const text = new TextDecoder("utf-8", { fatal: true }).decode(body);
    return /[֐-׿]/.test(text) ? null : "the body holds no Hebrew letter";
  } catch {
    return "the body is not valid UTF-8";
  }
}

async function problemOf(url: URL): Promise<string | null> {
  let problem: string | null = null;
  for (let attempt = 1; attempt <= retry.attempts; attempt += 1) {
    try {
      const response = await fetch(url, { cache: "no-store" });
      const body = new Uint8Array(await response.arrayBuffer());
      problem = textFileProblem(response.status, response.headers.get("content-type"), body);
    } catch (error) {
      problem = `no answer (${error instanceof Error ? error.message : String(error)})`;
    }
    if (problem === null) return null;
    if (attempt < retry.attempts) await new Promise((resolve) => setTimeout(resolve, retry.wait_seconds * 1000));
  }
  return problem;
}

async function main(base: string): Promise<void> {
  const failures: string[] = [];
  for (const file of [files.twin_file, files.llms_file]) {
    const url = new URL(file, base);
    const problem = await problemOf(url);
    console.log(`check-deploy: ${url} -> ${problem ?? "served as UTF-8, Hebrew readable"}`);
    if (problem !== null) failures.push(`${url}: ${problem}`);
  }
  if (failures.length > 0) throw new Error(`the live site serves text files wrongly:\n${failures.join("\n")}`);
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) await main(process.argv[2] ?? siteUrl);
