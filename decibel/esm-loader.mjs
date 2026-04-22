/**
 * ESM Loader to fix extensionless imports and JSON imports in @decibeltrade/sdk.
 * The SDK's dist/ files use `export * from "./admin"` without `.js` extension,
 * which Node.js 22+ requires for ESM. This loader auto-appends `.js`.
 * It also enables JSON imports with the correct assertion type.
 *
 * Usage: node --import ./esm-register.mjs executor.mjs
 */

import { resolve as pathResolve } from "node:path";

export async function resolve(specifier, context, nextResolve) {
  try {
    return await nextResolve(specifier, context);
  } catch (err) {
    if (err.code === "ERR_MODULE_NOT_FOUND" && !specifier.endsWith(".js") && !specifier.endsWith(".json")) {
      // Try appending .js
      try {
        return await nextResolve(specifier + ".js", context);
      } catch {
        // Try /index.js
        try {
          return await nextResolve(specifier + "/index.js", context);
        } catch {
          // Give up
        }
      }
    }
    throw err;
  }
}

export async function load(url, context, nextLoad) {
  // Handle .json files that lack import assertions
  if (url.endsWith(".json")) {
    const { readFileSync } = await import("node:fs");
    const { fileURLToPath } = await import("node:url");
    const filePath = fileURLToPath(url);
    const content = readFileSync(filePath, "utf-8");
    return {
      format: "module",
      shortCircuit: true,
      source: `export default ${content}`,
    };
  }
  return nextLoad(url, context);
}
