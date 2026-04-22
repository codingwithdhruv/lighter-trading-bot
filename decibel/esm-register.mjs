/**
 * ESM register hook — use with: node --import ./esm-register.mjs executor.mjs
 */
import { register } from "node:module";
import { pathToFileURL } from "node:url";

register("./esm-loader.mjs", pathToFileURL("./"));
