import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { compile } from "json-schema-to-typescript";

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const schemaPath = path.resolve(appRoot, "../../schemas/studio-protocol.schema.json");
const outputPath = path.resolve(appRoot, "src/generated/studio-protocol.d.ts");
const checkOnly = process.argv.includes("--check");

const schema = JSON.parse(await readFile(schemaPath, "utf8"));
const generated = await compile(schema, "StudioProtocolEnvelope", {
  bannerComment:
    "/* AUTO-GENERATED from schemas/studio-protocol.schema.json. Do not edit by hand. */",
  cwd: path.dirname(schemaPath),
  style: {
    bracketSpacing: true,
    printWidth: 100,
    semi: true,
    singleQuote: false,
    tabWidth: 2,
    trailingComma: "all",
    useTabs: false,
  },
  unreachableDefinitions: true,
});

let current = "";
try {
  current = await readFile(outputPath, "utf8");
} catch (error) {
  if (error?.code !== "ENOENT") {
    throw error;
  }
}

if (checkOnly) {
  if (current !== generated) {
    console.error("Generated Studio protocol types are stale. Run npm run generate:types.");
    process.exitCode = 1;
  }
} else if (current !== generated) {
  await mkdir(path.dirname(outputPath), { recursive: true });
  await writeFile(outputPath, generated, "utf8");
}
