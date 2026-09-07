import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
const require = createRequire(import.meta.url);
const ts = require("../.runtime/commerce-agents/examples/node_modules/typescript");
const source = readFileSync(new URL("../overlay/examples/retail/storefront-web/lib/checkout-decision-url.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText;
const { checkoutDecisionUrl } = await import("data:text/javascript;base64," + Buffer.from(compiled).toString("base64"));
const id = "a".repeat(36);
const path = `/api/conto/checkouts/${id}`;
test("local checkout summaries go to the API port", () => {
  assert.equal(checkoutDecisionUrl(`http://localhost:8000${path}`, "http://localhost:8000/api", "http://localhost:3000").href,
    `http://localhost:8000${path}/summary`);
});
test("hosted checkout summaries use the storefront origin", () => {
  assert.equal(checkoutDecisionUrl(path, "/api", "https://shop.example.com").href,
    `https://shop.example.com${path}/summary`);
});
test("handoff origin cannot redirect credentialed summary requests", () => {
  assert.equal(checkoutDecisionUrl(`https://untrusted.example${path}`, "http://localhost:8000/api", "http://localhost:3000").origin,
    "http://localhost:8000");
  assert.equal(checkoutDecisionUrl("/api/admin/keys", "/api", "https://shop.example.com"), null);
});
