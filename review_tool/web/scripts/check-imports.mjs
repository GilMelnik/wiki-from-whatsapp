// Enforces the folder rules of the web app (run by `npm run check`, before every build):
//
// - a tool (src/tools/<id>/...) imports packages, files of its own folder, and "@shell";
//   never another tool, never a shell file directly ("@shell/x" or "../../shell/x");
// - the shell (src/shell/...) never imports a tool;
// - every tool folder has an index.jsx.
//
// Exits 1 and lists every violation.

import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), "..", "src");
const TOOLS = join(SRC, "tools");
const SHELL = join(SRC, "shell");
const SOURCE_FILE = /\.(js|jsx)$/;
const IMPORT = /(?:^|[\s;])(?:import|export)\s[^"'`]*?from\s*["']([^"']+)["']|import\s*\(\s*["']([^"']+)["']\s*\)|^\s*import\s*["']([^"']+)["']/gm;

function sourceFiles(folder) {
  return readdirSync(folder).flatMap((name) => {
    const path = join(folder, name);
    if (statSync(path).isDirectory()) return sourceFiles(path);
    return SOURCE_FILE.test(name) ? [path] : [];
  });
}

function importsOf(file) {
  const text = readFileSync(file, "utf-8");
  return [...text.matchAll(IMPORT)].map((match) => match[1] ?? match[2] ?? match[3]);
}

function isInside(path, folder) {
  return path === folder || path.startsWith(folder + sep);
}

/** The violation for `specifier` imported by `file`, or null. */
export function violation(file, specifier) {
  const relativeImport = specifier.startsWith(".");
  const target = relativeImport ? resolve(dirname(file), specifier) : null;
  if (dirname(file) === TOOLS) {
    // src/tools/index.js, the list of tools: it imports each tool's entry and nothing else.
    const entry = target && relative(TOOLS, target).split(sep);
    const isToolEntry = entry && entry.length === 2 && entry[1] === "index.jsx";
    return target && !isToolEntry ? "the tool list imports only src/tools/<id>/index.jsx" : null;
  }
  if (isInside(file, TOOLS)) {
    const toolFolder = join(TOOLS, relative(TOOLS, file).split(sep)[0]);
    if (specifier.startsWith("@shell/")) return "deep import of the shell: import from \"@shell\"";
    if (target && !isInside(target, toolFolder)) {
      return isInside(target, SHELL)
        ? "imports a shell file directly: import from \"@shell\""
        : "imports outside its own tool folder";
    }
  }
  if (isInside(file, SHELL) && target && isInside(target, TOOLS)) return "the shell imports a tool";
  return null;
}

function main() {
  const problems = [];
  for (const file of sourceFiles(SRC)) {
    for (const specifier of importsOf(file)) {
      const problem = violation(file, specifier);
      if (problem) problems.push(`${relative(SRC, file)}: "${specifier}": ${problem}`);
    }
  }
  for (const name of readdirSync(TOOLS)) {
    const folder = join(TOOLS, name);
    if (statSync(folder).isDirectory() && !existsSync(join(folder, "index.jsx"))) {
      problems.push(`tools/${name}: missing index.jsx`);
    }
  }
  if (problems.length > 0) {
    console.error(`Import rules violated:\n  ${problems.join("\n  ")}`);
    process.exit(1);
  }
  console.log("Import rules: ok");
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main();
