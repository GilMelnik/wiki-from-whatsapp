import assert from "node:assert/strict";
import { join, resolve } from "node:path";
import { test } from "node:test";
import { violation } from "../scripts/check-imports.mjs";

const SRC = resolve(import.meta.dirname, "..", "src");
const toolFile = join(SRC, "tools", "pii", "index.jsx");
const shellFile = join(SRC, "shell", "App.jsx");

test("a tool may import packages, its own files and @shell", () => {
  assert.equal(violation(toolFile, "react"), null);
  assert.equal(violation(toolFile, "@shell"), null);
  assert.equal(violation(toolFile, "./ClaimList.jsx"), null);
  assert.equal(violation(join(SRC, "tools", "pii", "parts", "Row.jsx"), "../api.js"), null);
});

test("a tool may not reach into another tool or into shell files", () => {
  assert.match(violation(toolFile, "../threads/index.jsx"), /outside its own tool folder/);
  assert.match(violation(toolFile, "../../shell/api.js"), /shell file directly/);
  assert.match(violation(toolFile, "@shell/api.js"), /deep import/);
});

test("the tool list imports each tool's entry only", () => {
  const listFile = join(SRC, "tools", "index.js");
  assert.equal(violation(listFile, "./status/index.jsx"), null);
  assert.match(violation(listFile, "./status/ArtifactRow.jsx"), /only src\/tools\/<id>\/index.jsx/);
});

test("the shell may not import a tool", () => {
  assert.match(violation(shellFile, "../tools/index.js"), /the shell imports a tool/);
  assert.equal(violation(shellFile, "./api.js"), null);
});
