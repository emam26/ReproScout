import { readdir, readFile } from "node:fs/promises";
import { join } from "node:path";

const dist = new URL("../dist/", import.meta.url);
const indexPath = new URL("index.html", dist);
const index = await readFile(indexPath, "utf8");

if (!index.includes('src="/ReproScout/assets/')) {
  throw new Error("public build does not use the /ReproScout/ asset base");
}

const assetNames = (await readdir(dist)).includes("assets")
  ? await readdir(new URL("assets/", dist))
  : [];
const assetFiles = assetNames.filter((name) => /\.(?:css|html|js)$/i.test(name));

if (assetFiles.length === 0) {
  throw new Error("public build does not contain inspectable emitted assets");
}

const files = [
  ["dist/index.html", index],
  ...(await Promise.all(
    assetFiles.map(async (name) => [
      join("dist/assets", name),
      await readFile(new URL(`assets/${name}`, dist), "utf8"),
    ]),
  )),
];

const forbidden = [
  ["local API endpoint", /(?:127\.0\.0\.1|localhost)(?::\d+)?/i],
  ["FastAPI dependency", /\bfastapi\b/i],
  ["local API configuration", /apiConfig|\/api\//i],
  ["private evaluation reference", /private testing|evaluation results/i],
];

for (const [file, contents] of files) {
  for (const [label, pattern] of forbidden) {
    if (pattern.test(contents)) {
      throw new Error(`${label} found in ${file}`);
    }
  }
}

console.log(`verified static public build: ${assetFiles.length} emitted assets`);
