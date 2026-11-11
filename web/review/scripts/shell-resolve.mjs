// Module resolve hook registered by shell-alias.mjs: "@shell" → src/shell/pure.js.

const PURE_SHELL = new URL("../src/shell/pure.js", import.meta.url).href;

export async function resolve(specifier, context, nextResolve) {
  return nextResolve(specifier === "@shell" ? PURE_SHELL : specifier, context);
}
