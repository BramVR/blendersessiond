#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const source = path.join(root, "website");
const output = path.join(root, "dist", "site");
const hero = path.join(root, "docs", "assets", "blendersessiond-hero-v2.png");
const concept = path.join(root, "docs", "assets", "website-concept.png");

fs.rmSync(output, { recursive: true, force: true });
fs.cpSync(source, output, { recursive: true });
fs.copyFileSync(hero, path.join(output, "assets", path.basename(hero)));
if (!fs.existsSync(concept)) {
  throw new Error("missing ImageGen design reference: docs/assets/website-concept.png");
}
fs.writeFileSync(path.join(output, ".nojekyll"), "");

console.log(`built static site: ${path.relative(root, output)}`);
