import { createHash } from "node:crypto";
import { execFile } from "node:child_process";
import {
  cp,
  mkdtemp,
  mkdir,
  readFile,
  readdir,
  rm,
  stat,
  writeFile,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";

const execFileAsync = promisify(execFile);
const APP_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const PROTOCOL_ROOT = path.join(APP_ROOT, "protocol", "codex-app-server-0.144.6");
const VERSION = "0.144.6";
const MANIFEST_PATH = path.join(PROTOCOL_ROOT, "manifest.json");

const [mode = "--check", codexExecutable] = process.argv.slice(2);
if (mode === "--generate" || mode === "--check-generator") {
  if (!codexExecutable || !path.isAbsolute(codexExecutable)) {
    throw new Error(`${mode} requires an absolute Codex executable path`);
  }
  await verifyVersion(codexExecutable);
  const temporaryRoot = await mkdtemp(path.join(os.tmpdir(), "rwf-codex-protocol-"));
  try {
    const generated = await generate(codexExecutable, temporaryRoot);
    if (mode === "--generate") {
      await rm(PROTOCOL_ROOT, { recursive: true, force: true });
      await mkdir(PROTOCOL_ROOT, { recursive: true });
      await cp(generated.typescript, path.join(PROTOCOL_ROOT, "typescript"), {
        recursive: true,
        errorOnExist: true,
      });
      await cp(generated.jsonSchema, path.join(PROTOCOL_ROOT, "json-schema"), {
        recursive: true,
        errorOnExist: true,
      });
      await writeManifest();
    } else {
      await checkGeneratedTree(generated);
    }
  } finally {
    await rm(temporaryRoot, { recursive: true, force: true });
  }
}

await checkManifest();

async function verifyVersion(executable) {
  const { stdout } = await execFileAsync(executable, ["--version"], {
    encoding: "utf8",
    maxBuffer: 64 * 1024,
    timeout: 10_000,
    windowsHide: true,
  });
  if (stdout.trim() !== `codex-cli ${VERSION}`) {
    throw new Error(`Expected codex-cli ${VERSION}, received ${JSON.stringify(stdout.trim())}`);
  }
}

async function generate(executable, root) {
  const typescript = path.join(root, "typescript");
  const jsonSchema = path.join(root, "json-schema");
  await mkdir(typescript);
  await mkdir(jsonSchema);
  await runGenerator(executable, ["app-server", "generate-ts", "--out", typescript]);
  await runGenerator(executable, [
    "app-server",
    "generate-json-schema",
    "--out",
    jsonSchema,
  ]);
  await canonicalizeJsonTree(jsonSchema);
  return { typescript, jsonSchema };
}

async function runGenerator(executable, args) {
  await execFileAsync(executable, args, {
    encoding: "utf8",
    maxBuffer: 1024 * 1024,
    timeout: 60_000,
    windowsHide: true,
  });
}

async function canonicalizeJsonTree(root) {
  for (const relative of await listFiles(root)) {
    if (!relative.endsWith(".json")) {
      continue;
    }
    const filename = path.join(root, ...relative.split("/"));
    const parsed = JSON.parse(await readFile(filename, "utf8"));
    await writeFile(filename, `${JSON.stringify(sortJson(parsed), null, 2)}\n`, "utf8");
  }
}

function sortJson(value) {
  if (Array.isArray(value)) {
    return value.map(sortJson);
  }
  if (typeof value !== "object" || value === null) {
    return value;
  }
  return Object.fromEntries(
    Object.keys(value)
      .sort()
      .map((key) => [key, sortJson(value[key])]),
  );
}

async function writeManifest() {
  const artifacts = {
    json_schema: await treeIdentity(path.join(PROTOCOL_ROOT, "json-schema")),
    typescript: await treeIdentity(path.join(PROTOCOL_ROOT, "typescript")),
  };
  const manifest = {
    artifacts,
    codex_cli_version: VERSION,
    commands: {
      json_schema: ["app-server", "generate-json-schema", "--out", "<directory>"],
      typescript: ["app-server", "generate-ts", "--out", "<directory>"],
    },
    experimental: false,
    format: "rpg-world-forge.codex_app_server_protocol_provenance",
    format_version: 1,
    mcp_protocol_version: "2025-11-25",
  };
  await writeFile(MANIFEST_PATH, `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
}

async function checkManifest() {
  const manifest = JSON.parse(await readFile(MANIFEST_PATH, "utf8"));
  const expectedKeys = [
    "artifacts",
    "codex_cli_version",
    "commands",
    "experimental",
    "format",
    "format_version",
    "mcp_protocol_version",
  ];
  if (
    JSON.stringify(Object.keys(manifest).sort()) !== JSON.stringify(expectedKeys) ||
    manifest.format !== "rpg-world-forge.codex_app_server_protocol_provenance" ||
    manifest.format_version !== 1 ||
    manifest.codex_cli_version !== VERSION ||
    manifest.experimental !== false ||
    manifest.mcp_protocol_version !== "2025-11-25" ||
    manifest.commands?.typescript?.includes("--experimental") ||
    manifest.commands?.json_schema?.includes("--experimental")
  ) {
    throw new Error("Vendored Codex protocol manifest is invalid");
  }
  for (const [name, directory] of [
    ["typescript", "typescript"],
    ["json_schema", "json-schema"],
  ]) {
    const actual = await treeIdentity(path.join(PROTOCOL_ROOT, directory));
    if (JSON.stringify(actual) !== JSON.stringify(manifest.artifacts?.[name])) {
      throw new Error(`Vendored Codex ${name} protocol tree does not match its manifest`);
    }
  }
}

async function checkGeneratedTree(generated) {
  for (const [label, current, fresh] of [
    ["TypeScript", path.join(PROTOCOL_ROOT, "typescript"), generated.typescript],
    ["JSON Schema", path.join(PROTOCOL_ROOT, "json-schema"), generated.jsonSchema],
  ]) {
    const left = await treeIdentity(current);
    const right = await treeIdentity(fresh);
    if (JSON.stringify(left) !== JSON.stringify(right)) {
      throw new Error(`Codex ${label} generator output differs from the vendored protocol`);
    }
  }
}

async function treeIdentity(root) {
  const files = await listFiles(root);
  const hash = createHash("sha256");
  let bytes = 0;
  for (const relative of files) {
    const payload = await readFile(path.join(root, ...relative.split("/")));
    bytes += payload.length;
    hash.update(relative, "utf8");
    hash.update("\0");
    hash.update(String(payload.length), "ascii");
    hash.update("\0");
    hash.update(payload);
  }
  return { bytes, files: files.length, sha256: hash.digest("hex") };
}

async function listFiles(root) {
  const results = [];
  async function visit(directory, prefix) {
    const entries = await readdir(directory, { withFileTypes: true });
    for (const entry of entries.sort((left, right) => left.name.localeCompare(right.name))) {
      const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
      const absolute = path.join(directory, entry.name);
      if (entry.isDirectory()) {
        await visit(absolute, relative);
      } else if (entry.isFile()) {
        const info = await stat(absolute);
        if (info.nlink !== 1) {
          throw new Error(`Protocol artifact must be a standalone file: ${relative}`);
        }
        results.push(relative.normalize("NFC"));
      } else {
        throw new Error(`Protocol artifact tree contains a non-regular entry: ${relative}`);
      }
    }
  }
  await visit(root, "");
  return results.sort();
}
