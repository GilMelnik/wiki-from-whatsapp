// `node --import ./scripts/shell-alias.mjs --test tests/`: lets the pure modules of tools
// import "@shell" in tests, where Vite's alias does not exist. "@shell" resolves to the
// shell's pure entry (src/shell/pure.js); importing anything else from it fails loudly.

import { register } from "node:module";

register("./shell-resolve.mjs", import.meta.url);
