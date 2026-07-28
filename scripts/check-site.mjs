#!/usr/bin/env node
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const output = path.join(root, "dist", "site");
const html = fs.readFileSync(path.join(output, "index.html"), "utf8");
const css = fs.readFileSync(path.join(output, "styles.css"), "utf8");
const javascript = fs.readFileSync(path.join(output, "site.js"), "utf8");

const requiredFiles = [
  "index.html",
  "styles.css",
  "site.js",
  "llms.txt",
  "robots.txt",
  "sitemap.xml",
  "site.webmanifest",
  ".nojekyll",
  "assets/favicon.svg",
  "assets/blendersessiond-hero-v2.png",
];

for (const relative of requiredFiles) {
  assert.ok(fs.existsSync(path.join(output, relative)), `missing ${relative}`);
}

for (const reference of html.matchAll(/(?:href|src)="([^"]+)"/g)) {
  const target = reference[1];
  if (/^(?:#|https?:|mailto:)/.test(target)) continue;
  assert.ok(fs.existsSync(path.join(output, target)), `broken local reference: ${target}`);
}

assert.match(html, /<main id="main">/, "missing main landmark");
assert.match(html, /<h1 id="hero-title">/, "missing primary heading");
assert.match(html, /id="answers"/, "missing visible answer-focused content");
assert.match(
  html,
  /<link rel="canonical" href="https:\/\/blendersessiond\.bramvanrompuy\.be\/">/,
  "missing custom-domain canonical URL",
);
assert.doesNotMatch(html, /bramvr\.github\.io/, "metadata must use the custom domain");
const structuredDataMatch = html.match(
  /<script type="application\/ld\+json">\s*([\s\S]*?)\s*<\/script>/,
);
assert.ok(structuredDataMatch, "missing JSON-LD structured data");
const structuredData = JSON.parse(structuredDataMatch[1]);
assert.equal(structuredData["@context"], "https://schema.org");
assert.ok(
  structuredData["@graph"].some((entry) => entry["@type"] === "SoftwareSourceCode"),
  "missing SoftwareSourceCode structured data",
);
assert.match(css, /prefers-reduced-motion/, "missing reduced-motion styles");
assert.doesNotMatch(css, /fonts\.googleapis\.com/, "site must not depend on Google Fonts");
assert.match(javascript, /quickstartCommands/, "copy action must use command-only content");
assert.doesNotMatch(
  javascript,
  /writeText\(quickstart\.textContent\)/,
  "copy action must not include terminal prompts or output",
);
assert.doesNotMatch(
  javascript,
  /selectNodeContents\(quickstart\)/,
  "manual copy fallback must not select terminal prompts or output",
);
assert.doesNotMatch(
  html,
  /cd blendersessiond.*&amp;&amp;/,
  "quickstart commands must remain compatible with Windows PowerShell 5.1",
);

const robots = fs.readFileSync(path.join(output, "robots.txt"), "utf8");
const sitemap = fs.readFileSync(path.join(output, "sitemap.xml"), "utf8");
const llms = fs.readFileSync(path.join(output, "llms.txt"), "utf8");
assert.match(robots, /User-agent: OAI-SearchBot\s+Allow: \//, "OAI-SearchBot must be crawlable");
assert.match(
  robots,
  /Sitemap: https:\/\/blendersessiond\.bramvanrompuy\.be\/sitemap\.xml/,
  "robots.txt must advertise the custom-domain sitemap",
);
assert.match(
  sitemap,
  /<loc>https:\/\/blendersessiond\.bramvanrompuy\.be\/<\/loc>/,
  "sitemap must list the canonical URL",
);
assert.match(
  html,
  /rel="alternate" type="text\/markdown" href="llms\.txt"/,
  "page must advertise llms.txt",
);
assert.match(
  llms,
  /^# blendersessiond[\s\S]*## Canonical facts[\s\S]*## Primary resources/m,
  "llms.txt must provide a factual project summary and source map",
);

console.log(`checked static site: ${requiredFiles.length} files and local references`);
